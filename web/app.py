# Web Dashboard — Flask 本地总控
import json
import time
from pathlib import Path
from functools import cache

from flask import Flask, jsonify, render_template, request

from core.tool_registry import registry
from config import WEB_HOST, WEB_PORT

app = Flask(__name__, template_folder=Path(__file__).parent / "templates",
            static_folder=Path(__file__).parent / "static")

# 全局引用, 由 Orchestrator 注入
orchestrator = None


def init(orchestrator_ref):
    global orchestrator
    orchestrator = orchestrator_ref


# ===== API 接口 =====

@app.route("/api/sensors")
def api_sensors():
    """实时传感器数据"""
    data = {}
    for name in ("co2", "temperature", "light", "motion"):
        try:
            r = orchestrator.sensors.read(name)
            data[name] = {"value": r.value, "unit": r.unit, "timestamp": r.timestamp}
            if r.raw and "humidity" in r.raw:
                data["humidity"] = {"value": r.raw["humidity"], "unit": "%", "timestamp": r.timestamp}
        except Exception:
            data[name] = {"value": None, "unit": "", "timestamp": 0}
    return jsonify(data)


@app.route("/api/devices")
def api_devices():
    """设备状态 — 从实际设备驱动读取, 非fastpath缓存"""
    dev_status = orchestrator.devices.status_all()
    states = {}
    for name, info in dev_status.items():
        if name == "fan":
            states["fan"] = {"state": info.get("state", "off")}
        elif name == "light":
            states["light"] = {"state": info.get("state", "off"), "brightness": info.get("brightness", 0)}
        elif name == "ac":
            power = "on" if info.get("power") else "off"
            states["ac"] = {"state": f"{info.get('mode', 'cool')} {info.get('temp', 26)}°C {power}"}
        elif name == "air_purifier":
            level = info.get("level", 0)
            states["purifier"] = {"state": f"level {level}" if level > 0 else "off", "level": level}
    return jsonify(states)


@app.route("/api/sensor_log")
def api_sensor_log():
    """传感器历史曲线数据"""
    sensor = request.args.get("sensor", "co2")
    hours = int(request.args.get("hours", 24))
    data = orchestrator.db.query_sensor(sensor, hours)
    return jsonify(data)


@app.route("/api/events")
def api_events():
    """事件日志"""
    hours = int(request.args.get("hours", 24))
    events = orchestrator.db.query_events(hours)
    return jsonify(events)


@app.route("/api/foods")
def api_foods():
    """食材列表"""
    expiring = request.args.get("expiring")
    kwargs = {}
    if expiring:
        kwargs["expiring_days"] = int(expiring)
    foods = orchestrator.db.list_foods(**kwargs)
    return jsonify(foods)


@app.route("/api/foods/add", methods=["POST"])
def api_food_add():
    data = request.json
    fid = orchestrator.db.add_food(
        name=data["name"],
        expiry_date=data["expiry_date"],
        storage=data.get("storage", "冷藏"),
        quantity=data.get("quantity", 1),
        unit=data.get("unit", "个"),
    )
    return jsonify({"id": fid, "status": "ok"})


@app.route("/api/foods/remove", methods=["POST"])
def api_food_remove():
    data = request.json
    orchestrator.db.remove_food(data["id"])
    return jsonify({"status": "ok"})


@app.route("/api/command", methods=["POST"])
def api_command():
    """用户指令入口 -> Agent 路由 -> LLM -> 工具执行"""
    text = request.json.get("text", "")
    if not text:
        return jsonify({"reply": ""})

    if orchestrator is None:
        return jsonify({"reply": "系统未就绪", "actions": [], "agent": "", "llm_used": False})

    # 偏好学习: 如果最近60s内有AI决策且用户指令涉及设备控制, 记录为偏好覆盖
    try:
        recent = orchestrator.ai_brain._recent_decisions
        if recent:
            last = recent[-1]
            if time.time() - last.get("time", 0) < 60:
                last_tools = [a.get("tool", "") for a in last.get("actions", [])]
                device_kw = ("ac_control", "set_fan", "set_light", "set_air_purifier")
                if any(t in device_kw for t in last_tools) and any(
                    kw in text for kw in ("温度", "度", "风扇", "灯", "空调", "净化")
                ):
                    orchestrator.ai_brain.learn_preference(
                        trigger=f"ai_{'_'.join(last_tools[:2])}",
                        user_action=text[:80],
                    )
    except Exception:
        pass

    result = orchestrator.handle_command(text)
    return jsonify(result)


@app.route("/api/tools")
def api_tools():
    """已注册工具列表"""
    return jsonify(registry.list_tools())


@app.route("/api/status")
def api_status():
    """系统概览状态"""
    return jsonify({
        "uptime": time.time(),
        "sensors": len(orchestrator.sensors._sensors) if hasattr(orchestrator.sensors, '_sensors') else 0,
        "tools": len(registry.list_tools()),
        "llm_loaded": getattr(orchestrator, '_llm_available', False),
        "mock_mode": orchestrator.sensors._sensors.get("co2", None) is not None,
    })


@app.route("/api/insights")
def api_insights():
    """获取最近AI洞察"""
    events = orchestrator.db.query_events(24)
    insights = [e for e in events if e.get("event_type") == "ai_insight"]
    return jsonify(insights)


@app.route("/api/preferences")
def api_preferences():
    """查看学习到的用户偏好"""
    prefs = orchestrator.db.get_prefs(min_confidence=0.0)
    return jsonify(prefs)


@app.route("/api/preferences/<key>", methods=["DELETE"])
def api_preference_delete(key):
    """删除一个偏好"""
    orchestrator.db.delete_pref(key)
    return jsonify({"status": "ok"})


# ===== 主动反问 (v5.1) =====

@app.route("/api/pending_question")
def api_pending_question():
    """返回当前AI待答问题"""
    if orchestrator and orchestrator.ai_brain:
        q = orchestrator.ai_brain.get_pending_question()
        if q:
            return jsonify(q)
    return jsonify({})


@app.route("/api/answer_question", methods=["POST"])
def api_answer_question():
    """用户回复AI反问"""
    data = request.json or {}
    answer = data.get("answer", "")
    if not answer or not orchestrator or not orchestrator.ai_brain:
        return jsonify({"status": "ignored"})

    q = orchestrator.ai_brain.get_pending_question()
    if not q:
        return jsonify({"status": "no_question"})

    # 确认则执行pending工具
    positive = any(kw in answer for kw in ("是", "好", "可以", "行", "yes", "ok", "通风"))
    if positive and q.get("pending_tools"):
        try:
            actions = registry.execute_plan(q["pending_tools"])
            orchestrator.ai_brain.clear_pending_question()
            return jsonify({"status": "executed", "actions": actions})
        except Exception as e:
            return jsonify({"status": "error", "detail": str(e)})

    orchestrator.ai_brain.clear_pending_question()
    return jsonify({"status": "dismissed"})


# ===== 页面 =====

@app.route("/")
def index():
    return render_template("dashboard.html")


def run():
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
