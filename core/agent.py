# Agent 编排器 — 主循环
import asyncio
import logging
import threading
import time
from pathlib import Path

from config import (
    SENSOR_INTERVAL, AGENT, MOCK_SENSORS, LLM_MODEL, WEB_HOST, WEB_PORT,
    I2C_BUS, UART_DEV, SENSOR_CO2, SENSOR_TEMP, GPIO, GPIO_CHIP,
    AI_EVAL_INTERVAL, AI_ANOMALY_THRESHOLD,
    K210_UART, K210_BAUD,
)
from core.fastpath import FastPathEngine, setup_rules
from core.tool_registry import registry
from core.midpath import MidPathHandler
from core.command_handler import CommandHandler
from sensors.base import SensorManager
from devices.manager import DeviceManager
from devices.fan import FanDevice
from devices.ac import ACDevice
from devices.light import LightDevice
from devices.purifier import AirPurifierDevice
from knowledge.database import Database
from tts.speaker import TTSEngine
from core.ai_brain import AIBrain
from core.scene_engine import SceneEngine
from core.profile_engine import ProfileEngine

logger = logging.getLogger("agent")


class AgentOrchestrator:
    def __init__(self):
        self.fastpath = FastPathEngine()
        self.sensors = SensorManager()
        self.devices = DeviceManager()
        self.db = Database()
        self.tts = TTSEngine()
        self.midpath = MidPathHandler()
        self.ai_brain = AIBrain()
        self.scene_engine = SceneEngine()
        self.profile_engine = ProfileEngine(db=self.db)
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._llm_available = False
        self._web_thread: threading.Thread = None

    async def start(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )
        logger.info("Agent ZX 启动中...")

        # 1. 注册工具
        self._register_tools()

        # 2. 初始化传感器
        self._init_sensors()

        # 3. 注册设备驱动
        self._register_devices()

        # 4. 配置规则引擎
        setup_rules(self.fastpath)

        # 5. 尝试加载 LLM
        await self._init_llm()

        # 5.2 接入用户画像引擎
        self.profile_engine._db = self.db
        self.ai_brain.set_profile_engine(self.profile_engine)
        # 重建画像（基于已有数据）
        self.profile_engine.update_profile()
        logger.info("用户画像引擎已就绪")

        # 5.5 接入AI大脑
        if self._llm_available:
            self.ai_brain.set_llm(self.llm)
            self.ai_brain._db = self.db
            self.ai_brain._sensors = self.sensors
            self.midpath.set_llm(self.llm)
            logger.info("AI大脑已接入LLM")

        # 6. 初始化命令处理器
        self._cmd_handler = CommandHandler(
            llm=self.llm if hasattr(self, 'llm') and self._llm_available else None,
            db=self.db,
            sensors=self.sensors,
        )

        # 7. 启动 Web Dashboard
        self._start_web()

        logger.info("Agent ZX 就绪")

    async def run(self):
        self._running = True

        # 启动传感器轮询任务
        for sensor_name, interval in SENSOR_INTERVAL.items():
            self._tasks.append(
                asyncio.create_task(self._poll_sensor(sensor_name, interval))
            )

        # 按键监听 (mock 模式用模拟)
        self._tasks.append(asyncio.create_task(self._doorbell_listener()))

        # 定时任务
        self._tasks.append(asyncio.create_task(self._scheduler()))

        # AI 主动决策循环
        if self._llm_available:
            self._tasks.append(asyncio.create_task(self._ai_poll_cycle()))

        # 等待所有任务 (KeyboardInterrupt 时退出)
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.sensors.cleanup_all()
        self.devices.cleanup_all()
        self._stop_web()
        self.db.close()
        logger.info("Agent ZX 已停止")

    # ---- 公开接口 ----

    def handle_command(self, text: str, history: list[dict] | None = None) -> dict:
        """处理用户自然语言指令, 返回 {reply, actions, agent, llm_used}"""
        return self._cmd_handler.handle(text, history)

    # ---- 内部 ----

    def _register_tools(self):
        # 传感器工具
        @registry.register()
        def read_co2() -> float:
            return self.sensors.read("co2").value

        @registry.register()
        def read_temperature() -> float:
            return self.sensors.read("temperature").value

        @registry.register()
        def read_humidity() -> float:
            return self.sensors.read("temperature").raw.get("humidity", 50)

        @registry.register()
        def read_light() -> float:
            return self.sensors.read("light").value

        @registry.register(description="读取K210摄像头状态: 人物/手势/人脸数/条码")
        def read_k210() -> dict:
            r = self.sensors.read("k210")
            return r.raw

        @registry.register()
        def read_person_present() -> bool:
            return self.sensors.read("motion").value > 0.5

        # 设备控制
        @registry.register(description="风扇控制 0关-3高速")
        def set_fan(speed: int):
            result = self.devices.control("fan", "set", speed=speed)
            self.fastpath.set_device_state("fan", ["off", "low", "mid", "high"][max(0, min(3, speed))])
            return result

        @registry.register(description="空调控制 mode=cool/dry/heat")
        def ac_control(mode: str, temp: int = 26, fan_speed: str = "auto"):
            result = self.devices.control("ac", mode, temp=temp, fan_speed=fan_speed)
            self.fastpath.set_device_state("ac", "on")
            return result

        @registry.register(description="灯光控制 on/off, brightness=0-255")
        def set_light(state: str, brightness: int = 255):
            result = self.devices.control("light", state, brightness=brightness)
            self.fastpath.set_device_state("light", state)
            return result

        @registry.register(description="空气净化 0关-3强")
        def set_air_purifier(level: int):
            result = self.devices.control("air_purifier", "set", level=level)
            self.fastpath.set_device_state("purifier", f"level {level}" if level > 0 else "off")
            return result

        # 食材管理
        @registry.register(description="食材入库: name, expiry_date(Y-m-d), storage")
        def add_food(name: str, expiry_date: str, storage: str = "冷藏",
                     quantity: float = 1, unit: str = "个"):
            fid = self.db.add_food(name, expiry_date, storage, quantity, unit)
            self.db.log_event("food_add", f"{name} → {expiry_date}")
            return f"已记录: {name}, {expiry_date}到期, {storage}"

        @registry.register(description="列出食材, expiring_days=几天内过期")
        def list_foods(storage: str = None, expiring_days: int = None) -> list:
            return self.db.list_foods(expiring_days)

        @registry.register(description="搜索食材")
        def search_food(keyword: str) -> list:
            return self.db.search_food(keyword)

        # 查询
        @registry.register(description="查询传感器历史, sensor=co2/temp/light, hours=小时")
        def query_sensor_log(sensor: str, hours: int = 24) -> list:
            return self.db.query_sensor(sensor, hours)

        @registry.register(description="查询事件日志")
        def query_event_log(hours: int = 24) -> list:
            return self.db.query_events(hours)

        @registry.register(description="生成日报/总结: 最近24h传感器均值+事件摘要")
        def generate_daily_report() -> str:
            """日报: 传感器均值 + 事件摘要"""
            lines = ["=== 家庭日报 ==="]
            for sensor in ("co2", "temperature", "light"):
                data = self.db.query_sensor(sensor, 24)
                if data:
                    vals = [d["value"] for d in data]
                    avg = sum(vals) / len(vals)
                    lines.append(f"{sensor}: 均值 {avg:.1f}")
            events = self.db.query_events(24)
            if events:
                lines.append(f"事件: {len(events)} 条")
                for e in events[:5]:
                    lines.append(f"  {e.get('detail', '')[:40]}")
            else:
                lines.append("事件: 无")
            return "\n".join(lines)

        # 通知
        @registry.register(description="TTS 语音播报")
        def tts(text: str):
            self.tts.speak(text)
            return f"已播报: {text}"

        @registry.register(description="屏显通知")
        def notify_display(title: str, body: str = ""):
            logger.info("通知: %s - %s", title, body)

        # 天气 (mock)
        @registry.register(description="获取天气 {temp, humidity, condition}")
        def get_weather() -> dict:
            return {"temp": 22, "humidity": 60, "condition": "晴天"}

        # 家庭贴士
        @registry.register(description="记录家庭小贴士")
        def add_home_tip(category: str, content: str):
            tid = self.db.add_tip(category, content)
            return f"已记录: {content}"

        @registry.register(description="列出家庭小贴士")
        def list_home_tips(category: str = None) -> list:
            return self.db.list_tips(category)

        logger.info("已注册 %d 个工具", len(registry.list_tools()))

    def _init_sensors(self):
        if MOCK_SENSORS:
            from sensors.mock import MockCO2, MockTempHumid, MockLight, MockMotion
            from sensors.k210 import MockK210Sensor
            self.sensors.register(MockCO2())
            self.sensors.register(MockTempHumid())
            self.sensors.register(MockLight())
            self.sensors.register(MockMotion())
            self.sensors.register(MockK210Sensor())
            logger.info("使用 MOCK 传感器 (含 K210)")
        else:
            from sensors.light import LightSensor
            from sensors.motion import MotionSensor

            # CO₂ 传感器: MH-Z19B (UART) / SGP30 (I2C)
            if SENSOR_CO2 == "sgp30":
                from sensors.sgp30 import SGP30Sensor
                self.sensors.register(SGP30Sensor(bus=I2C_BUS))
                logger.info("CO₂ 传感器: SGP30 (I2C bus %d)", I2C_BUS)
            else:
                from sensors.co2 import CO2Sensor
                self.sensors.register(CO2Sensor(device=UART_DEV))
                logger.info("CO₂ 传感器: MH-Z19B (UART %s)", UART_DEV)

            # 温湿度传感器: SHT30 (I2C) / DHT11 (GPIO)
            if SENSOR_TEMP == "dht11":
                from sensors.dht11 import DHT11Sensor
                self.sensors.register(DHT11Sensor(pin=GPIO["dht11"]))
                logger.info("温湿度传感器: DHT11 (GPIO pin %d)", GPIO["dht11"])
            else:
                from sensors.temp_humid import TempHumidSensor
                self.sensors.register(TempHumidSensor(bus=I2C_BUS))
                logger.info("温湿度传感器: SHT30 (I2C bus %d)", I2C_BUS)

            self.sensors.register(LightSensor(bus=I2C_BUS))
            self.sensors.register(MotionSensor())
            logger.info("光照: BH1750, 人体: HC-SR501")

            # K210 摄像头 (UART)
            from sensors.k210 import K210Sensor
            self.sensors.register(K210Sensor(device=K210_UART, baud=K210_BAUD))
            logger.info("K210 摄像头: %s @ %d", K210_UART, K210_BAUD)

    def _register_devices(self):
        self.devices.register(FanDevice())
        self.devices.register(ACDevice())
        self.devices.register(LightDevice())
        self.devices.register(AirPurifierDevice())
        logger.info("设备驱动注册完成")

    async def _init_llm(self):
        if not LLM_MODEL or not Path(LLM_MODEL).exists():
            logger.warning("LLM 模型未找到 (%s), SlowPath 降级为规则", LLM_MODEL)
            return
        try:
            from llm.engine import LLMEngine
            self.llm = LLMEngine(LLM_MODEL)
            self._llm_available = True
            logger.info("LLM 加载完成")
        except Exception as e:
            logger.warning("LLM 加载失败: %s", e)
            self._llm_available = False

    def _start_web(self):
        try:
            from web.app import app as web_app, init as web_init
            web_init(self)
            import threading
            self._web_thread = threading.Thread(
                target=web_app.run,
                kwargs={"host": WEB_HOST, "port": WEB_PORT, "debug": False, "use_reloader": False},
                daemon=True,
            )
            self._web_thread.start()
            logger.info("Web Dashboard: http://%s:%d", WEB_HOST, WEB_PORT)
        except Exception as e:
            logger.warning("Web Dashboard 启动失败: %s", e)

    def _stop_web(self):
        # Flask 无原生优雅停止, daemon thread 随主进程退出
        pass

    async def _poll_sensor(self, name: str, interval: int):
        while self._running:
            try:
                reading = self.sensors.read(name)
                old = self.fastpath.get_sensor(name)

                # 异常检测: 读数跳变时跳过FastPath触发
                anomaly = False
                if old is not None and self._llm_available:
                    anomaly = self.ai_brain.detect_anomaly(name, old, reading.value)
                    if anomaly:
                        logger.warning("ANOMALY: %s %.1f→%.1f 跳过自动操作", name, old, reading.value)
                        self.db.log_event("anomaly", f"{name}: {old:.1f}→{reading.value:.1f}")

                # 记录到数据库
                self.db.log_sensor(name, reading.value, reading.unit)

                # 推送 FastPath (仅非异常时)
                if not anomaly:
                    self.fastpath.update_sensor(name, reading.value)
                # 处理湿度 (异常检查后仍记录，但不触发规则)
                if reading.raw and "humidity" in reading.raw:
                    self.db.log_sensor("humidity", reading.raw["humidity"], "%")
                    if not anomaly:
                        self.fastpath.update_sensor("humidity", reading.raw["humidity"])
            except Exception as e:
                logger.error("传感器 %s 读取失败: %s", name, e)
            await asyncio.sleep(interval)

    async def _doorbell_listener(self):
        """门铃按键监听: GPIO 下降沿触发 → TTS播报 + 事件记录"""
        pin = GPIO.get("doorbell_btn")
        if pin is None:
            logger.warning("doorbell_btn 未配置 GPIO")
            return

        if MOCK_SENSORS:
            # 开发模式: 启动后 15s 模拟一次按键
            await asyncio.sleep(15)
            if self._running:
                logger.info("mock: 外卖按键触发")
                self.tts.speak("有客到")
                self.db.log_event("doorbell", "外卖按键触发")
            return

        # 真实 GPIO 模式 (gpiod v2/v1/sysfs 三档降级)
        import os
        import contextlib

        _DEBOUNCE_S = 2.0
        _POLL_INTERVAL = 0.1
        _EVENT_TIMEOUT = 1.0
        last_trigger = 0.0

        # 检测可用 GPIO 库
        gpiod_v2 = False
        gpiod_v1 = False
        try:
            import gpiod
            if hasattr(gpiod, "LineSettings"):
                from gpiod.line import Direction, Edge
                gpiod_v2 = True
            else:
                gpiod_v1 = True
        except ImportError:
            pass

        request = None
        line = None
        sysfs_path = None

        try:
            # --- gpiod v2 (Pi 5 / gpiod >= 2.0) ---
            if gpiod_v2:
                try:
                    chip = gpiod.Chip(GPIO_CHIP)
                    request = chip.request_lines(
                        consumer="doorbell",
                        config={pin: gpiod.LineSettings(
                            direction=Direction.INPUT,
                            edge_detection=Edge.FALLING,
                        )},
                    )
                    logger.info("门铃 GPIO (gpiod v2): %s pin %d", GPIO_CHIP, pin)
                    while self._running:
                        edges = request.read_edge_events(_EVENT_TIMEOUT)
                        for ev in edges:
                            now = time.time()
                            if now - last_trigger > _DEBOUNCE_S:
                                last_trigger = now
                                self._doorbell_trigger()
                        if not edges:
                            await asyncio.sleep(0)
                except Exception as e:
                    logger.debug("gpiod v2 门铃失败: %s", e)
                    request = None

            # --- gpiod v1 (旧版 Pi / gpiod < 2.0) ---
            if not request and gpiod_v1:
                try:
                    chip = gpiod.Chip(GPIO_CHIP)
                    line = chip.get_line(pin)
                    line.request(consumer="doorbell", type=gpiod.LINE_REQ_EV_FALLING_EDGE)
                    logger.info("门铃 GPIO (gpiod v1): %s pin %d", GPIO_CHIP, pin)
                    while self._running:
                        if line.event_wait(nsec=int(_EVENT_TIMEOUT * 1e9)):
                            event = line.event_read()
                            if event.type == gpiod.LineEvent.FALLING_EDGE:
                                now = time.time()
                                if now - last_trigger > _DEBOUNCE_S:
                                    last_trigger = now
                                    self._doorbell_trigger()
                        await asyncio.sleep(0)
                except Exception as e:
                    logger.debug("gpiod v1 门铃失败: %s", e)
                    line = None

            # --- sysfs fallback (无 gpiod) ---
            if not request and not line:
                gpio_dir = f"/sys/class/gpio/gpio{pin}"
                if not os.path.exists(gpio_dir):
                    try:
                        with open("/sys/class/gpio/export", "w") as f:
                            f.write(str(pin))
                        for _ in range(20):
                            if os.path.exists(gpio_dir):
                                break
                            time.sleep(0.05)
                    except OSError:
                        pass
                if os.path.exists(gpio_dir):
                    with open(f"{gpio_dir}/direction", "w") as f:
                        f.write("in")
                    with open(f"{gpio_dir}/edge", "w") as f:
                        f.write("falling")
                    sysfs_path = f"{gpio_dir}/value"
                    logger.info("门铃 GPIO (sysfs): pin %d", pin)
                    prev_val = "1"
                    while self._running:
                        try:
                            with open(sysfs_path) as f:
                                val = f.read().strip()
                            if prev_val == "1" and val == "0":
                                now = time.time()
                                if now - last_trigger > _DEBOUNCE_S:
                                    last_trigger = now
                                    self._doorbell_trigger()
                            prev_val = val
                        except OSError:
                            pass
                        await asyncio.sleep(_POLL_INTERVAL)

        finally:
            if request:
                request = None
            if line:
                line = None
            if sysfs_path:
                try:
                    with open("/sys/class/gpio/unexport", "w") as f:
                        f.write(str(pin))
                except Exception:
                    pass

    def _doorbell_trigger(self):
        """门铃触发动作"""
        logger.info("门铃按键触发")
        self.tts.speak("有客到")
        self.db.log_event("doorbell", "门铃按键触发")

    async def _handle_k210_events(self):
        """处理 K210 摄像头事件: 人物进出→场景触发, 手势→设备控制"""
        if not self.sensors.has("k210"):
            return
        try:
            k210 = self.sensors._sensors.get("k210")
            if k210 is None:
                return

            # 手势 → 设备控制
            gesture = k210.gesture
            if gesture:
                logger.info("K210 手势: %s", gesture)
                gesture_actions = {
                    "wave_up": [{"tool": "set_light", "params": {"state": "on", "brightness": 255}}],
                    "wave_down": [{"tool": "set_light", "params": {"state": "off"}}],
                    "wave_left": [{"tool": "set_fan", "params": {"speed": 0}}],
                    "wave_right": [{"tool": "set_fan", "params": {"speed": 3}}],
                }
                actions = gesture_actions.get(gesture)
                if actions:
                    registry.execute_plan(actions)
                    self.db.log_event("k210_gesture", f"gesture:{gesture} → {actions}")

            # 告警事件
            alert = k210.alert
            if alert:
                logger.warning("K210 告警: %s", alert)
                self.db.log_event("k210_alert", str(alert))

            # 人物进入 → 欢迎场景
            last_event = k210.last_event
            if last_event.get("event") == "enter":
                person = last_event.get("person", "unknown")
                logger.info("K210 人物进入: %s", person)
                welcome_plan = [
                    {"tool": "set_light", "params": {"state": "on", "brightness": 200}},
                ]
                registry.execute_plan(welcome_plan)
                self.db.log_event("k210_enter", person)
                if hasattr(k210, "send_lcd"):
                    k210.send_lcd(f"Welcome {person}", 0x07E0)

            # 人物离开 → 离家场景 (仅当无人时)
            if last_event.get("event") == "leave" and not k210.person_present:
                logger.info("K210 所有人已离开")
                away_plan = [
                    {"tool": "set_light", "params": {"state": "off"}},
                    {"tool": "set_fan", "params": {"speed": 0}},
                ]
                registry.execute_plan(away_plan)
                self.db.log_event("k210_leave", "all_left")

        except Exception as e:
            logger.debug("K210 事件处理异常: %s", e)

    async def _scheduler(self):
        """定时任务调度"""
        while self._running:
            now = time.localtime()
            t = f"{now.tm_hour:02d}:{now.tm_min:02d}"

            if t == "08:00":
                await self._check_expiring_foods()
                if self._llm_available:
                    await self._ai_daily_insight()
            elif t == "07:00":
                await self._daily_dress_advice()

            await asyncio.sleep(30)  # 每 30s 检查一次

    async def _ai_poll_cycle(self):
        """AI主动决策循环: 每AI_EVAL_INTERVAL秒读取传感器快照→LLM决定行动"""
        while self._running:
            try:
                snapshot = {}
                for name in ("co2", "temperature", "light"):
                    try:
                        r = self.sensors.read(name)
                        snapshot[name] = r.value
                    except Exception:
                        pass
                try:
                    r = self.sensors.read("temperature")
                    if r.raw and "humidity" in r.raw:
                        snapshot["humidity"] = r.raw["humidity"]
                except Exception:
                    pass
                try:
                    snapshot["person_present"] = self.sensors.read("motion").value > 0.5
                except Exception:
                    snapshot["person_present"] = False

                # K210 摄像头事件处理 (人物进出/手势/告警)
                await self._handle_k210_events()

                # 场景自动触发 (v5.1): 深夜无人无灯 → 睡觉场景
                try:
                    light_on = snapshot.get("light", 0) > 50
                    motion = snapshot.get("person_present", False)
                    hour = time.localtime().tm_hour
                    auto_scene = self.scene_engine.check_auto_trigger(hour, motion, light_on)
                    if auto_scene:
                        logger.info("自动场景: %s", auto_scene["name"])
                        registry.execute_plan(auto_scene["tools"])
                        if self.db:
                            self.db.log_event("scene_trigger",
                                f"auto:{auto_scene['name']}")
                        await asyncio.sleep(AI_EVAL_INTERVAL)
                        continue
                except Exception as e:
                    logger.warning("场景自动触发异常: %s", e)

                result = self.ai_brain.evaluate(snapshot)

                if result.get("type") in ("question", "proactive_suggestion"):
                    logger.info("AI%s: %s", "反问" if result.get("type") == "question" else "建议", result.get("text"))
                elif result.get("type") == "action":
                    tool_chain = result.get("tool_chain", [])
                    explanation = result.get("explanation", "")
                    if tool_chain:
                        logger.info("AI决策: %s → %s", explanation, tool_chain)
                        try:
                            actions = registry.execute_plan(tool_chain)
                        except Exception as e:
                            logger.error("AI工具执行失败: %s", e)
                            actions = []

                        if actions and self._llm_available:
                            context = {
                                "temp": snapshot.get("temperature", "?"),
                                "humidity": snapshot.get("humidity", "?"),
                                "co2": snapshot.get("co2", "?"),
                            }
                            try:
                                supplement = await self.midpath.supplement_ai_decision(
                                    explanation, actions, context
                                )
                                logger.info("AI补充: %s", supplement)
                            except Exception as e:
                                logger.warning("MidPath补充失败: %s", e)

                        if self.db:
                            for a in actions:
                                self.db.log_event("ai_action",
                                    f"{a.get('tool')}: {str(a.get('result', ''))[:80]}")
                else:
                    logger.debug("AI评估: 无需操作")
            except Exception as e:
                logger.error("AI评估周期异常: %s", e, exc_info=True)
            await asyncio.sleep(AI_EVAL_INTERVAL)

    async def _ai_daily_insight(self):
        """AI生成24h趋势洞察"""
        try:
            trends = {}
            for sensor in ("co2", "temperature", "light"):
                data = self.db.query_sensor(sensor, 24)
                if data:
                    trends[sensor] = [d["value"] for d in data]
            events = self.db.query_events(24)
            insight = self.ai_brain.generate_insight(trends, events)
            if insight:
                logger.info("AI洞察: %s", insight)
                self.tts.speak(insight)
                self.db.log_event("ai_insight", insight)
        except Exception as e:
            logger.error("AI洞察生成失败: %s", e)

    async def _check_expiring_foods(self):
        expiring = self.db.list_foods(expiring_days=1)
        if expiring:
            names = [f["name"] for f in expiring]
            msg = f"提醒: {', '.join(names)} 今天过期"
            self.tts.speak(msg)
            self.db.log_event("food_reminder", msg)
            logger.info("食材到期提醒: %s", names)

    async def _daily_dress_advice(self):
        try:
            temp = self.sensors.read("temperature").value
        except Exception:
            temp = 25
        if temp > 28:
            msg = f"今天室内{temp:.0f}°C, 建议穿短袖"
        elif temp < 15:
            msg = f"室内{temp:.0f}°C 较冷, 建议穿外套"
        else:
            msg = f"室内{temp:.0f}°C, 体感舒适"
        self.tts.speak(msg)
        self.db.log_event("dress_advice", msg)



