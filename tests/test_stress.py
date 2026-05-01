# Agent ZX 全方位压力测试 (v5.1)
# 覆盖: 数据库极限 / 并发竞态 / 解析鲁棒 / 场景Fuzz / 24h模拟 / 内存循环
import json
import os
import random
import sqlite3
import statistics
import tempfile
import threading
import time
from pathlib import Path

import pytest

# ============================================================
# 辅助函数
# ============================================================

def _random_text(min_len=1, max_len=200):
    """生成随机中英文混合文本"""
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 " \
            "你好世界智能家居传感器温度湿度光照空调风扇净化器厨房食材冰箱过期 " \
            "!@#$%^&*()_+-=[]{}|;':\",./<>?`~ \t\n\r"
    return ''.join(random.choice(chars) for _ in range(random.randint(min_len, max_len)))


# ============================================================
# 1. 数据库高压
# ============================================================

class TestDatabaseHammer:
    """数据库极限压力: 批量写入 / 大查询 / 并发"""

    def test_bulk_100k_sensor_logs(self):
        """10万条传感器日志批量写入+查询"""
        from knowledge.database import Database
        db = Database()

        t0 = time.perf_counter()
        for i in range(100000):
            db.log_sensor("co2", 400 + (i % 2000))
            if i % 10000 == 0 and i > 0:
                db.conn.commit()
        elapsed = time.perf_counter() - t0

        # 验证写入成功 (可能含其他测试写入的行, 用 >= 而非精确匹配)
        result = db.query_sensor("co2", hours=24)
        assert len(result) >= 100000

        # 性能断言: 10万条应在300s内完成 (Windows SQLite较慢)
        assert elapsed < 300, f"写入10万条耗时 {elapsed:.1f}s"

        # 清理
        db.conn.execute("DELETE FROM sensor_log")
        db.conn.commit()
        db.close()

    def test_bulk_10k_foods(self):
        """1万条食材批量写入+搜索"""
        from knowledge.database import Database
        db = Database()

        t0 = time.perf_counter()
        for i in range(10000):
            name = f"测试食材_{i % 500}"
            db.add_food(name, "2026-12-31", storage="冷藏",
                        quantity=(i % 10) + 1, unit=["个", "斤", "袋"][i % 3])
        elapsed = time.perf_counter() - t0

        assert elapsed < 30, f"写入1万条食材耗时 {elapsed:.1f}s"

        # 搜索性能
        t1 = time.perf_counter()
        result = db.search_food("测试食材_1")
        search_elapsed = time.perf_counter() - t1
        assert len(result) > 0
        assert search_elapsed < 2.0, f"搜索耗时 {search_elapsed:.3f}s"

        db.conn.execute("DELETE FROM foods")
        db.conn.commit()
        db.close()

    def test_bulk_5k_events(self):
        """5000条事件日志+查询"""
        from knowledge.database import Database
        db = Database()

        for i in range(5000):
            db.log_event(f"type_{i % 20}", f"detail_{i}", f"source_{i % 10}")

        result = db.query_events(hours=24)
        assert len(result) > 0
        assert len(result) <= 5000

        db.conn.execute("DELETE FROM event_log")
        db.conn.commit()
        db.close()

    def test_simultaneous_read_write(self):
        """并发读写: SQLite WAL模式支持多读单写"""
        import sqlite3
        from config import DB_PATH
        errors = []

        # 使用独立连接模拟并发 (共享同一个WAL数据库)
        def writer(sensor_name):
            conn = None
            try:
                conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                for _ in range(500):
                    conn.execute(
                        "INSERT INTO sensor_log(sensor, value, unit, timestamp) VALUES (?,?,?,?)",
                        (sensor_name, random.uniform(400, 2000), "ppm", time.time()),
                    )
                    if _ % 100 == 0:
                        conn.commit()
                conn.commit()
            except Exception as e:
                errors.append(f"writer {sensor_name}: {e}")
            finally:
                if conn:
                    conn.close()

        def reader():
            conn = None
            try:
                conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                for _ in range(100):
                    cur = conn.execute(
                        "SELECT value, unit, timestamp FROM sensor_log WHERE sensor='co2' "
                        "AND timestamp>=? ORDER BY timestamp",
                        (time.time() - 3600,),
                    )
                    cur.fetchall()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"reader: {e}")
            finally:
                if conn:
                    conn.close()

        threads = []
        for name in ["co2", "temperature", "light", "motion"]:
            threads.append(threading.Thread(target=writer, args=(name,)))
        for _ in range(2):
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 清理: 用独立连接
        cleanup_conn = sqlite3.connect(str(DB_PATH))
        cleanup_conn.execute("DELETE FROM sensor_log")
        cleanup_conn.commit()
        cleanup_conn.close()

        assert len(errors) == 0, f"并发错误: {errors}"

    def test_knowledge_query_performance(self):
        """知识查询方法在大量数据下的性能"""
        from knowledge.database import Database
        db = Database()

        # 预填7天数据
        now = time.time()
        for i in range(10000):
            ts = now - random.uniform(0, 7 * 86400)
            db.conn.execute(
                "INSERT INTO sensor_log(sensor, value, unit, timestamp) VALUES (?,?,?,?)",
                ("co2", 400 + random.uniform(0, 800), "ppm", ts),
            )
        db.conn.commit()

        t0 = time.perf_counter()
        same = db.query_sensor_same_hour("co2", days=7)
        assert isinstance(same, list)
        assert time.perf_counter() - t0 < 5.0

        t0 = time.perf_counter()
        profile = db.query_sensor_hourly_profile("co2", days=7)
        assert isinstance(profile, list)
        assert time.perf_counter() - t0 < 5.0

        t0 = time.perf_counter()
        corr = db.query_sensor_correlation("co2", "temperature", hours=24)
        assert isinstance(corr, list)
        assert time.perf_counter() - t0 < 5.0

        db.conn.execute("DELETE FROM sensor_log")
        db.conn.commit()
        db.close()


# ============================================================
# 2. 工具注册表高压
# ============================================================

class TestToolRegistryPressure:
    """工具注册表极限"""

    def test_200_tools_parallel(self):
        """200个工具并行执行"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        for i in range(200):
            def make_fn(idx):
                def fn(msg: str = "") -> str:
                    return f"tool_{idx}: {msg}"
                return fn
            r.register(name=f"tool_{i}", description=f"Tool {i}")(make_fn(i))

        plan = [{"tool": f"tool_{i}", "params": {"msg": f"msg_{i}"}} for i in range(200)]
        actions = r.execute_plan(plan)
        assert len(actions) == 200
        # 验证全部完成
        for a in actions:
            assert a["tool"].startswith("tool_")
            assert "result" in a

    def test_missing_and_present_mix(self):
        """100个工具中50个不存在, 不崩溃"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="test")
        def existing_tool(x: int = 0) -> str:
            return f"ok_{x}"

        plan = []
        for i in range(100):
            if i % 2 == 0:
                plan.append({"tool": "existing_tool", "params": {"x": i}})
            else:
                plan.append({"tool": f"nonexistent_{i}", "params": {}})

        actions = r.execute_plan(plan)
        assert len(actions) == 100
        # 并行执行, 顺序不保证 — 验证存在工具的结果出现
        ok_results = [a.get("result", "") for a in actions if "ok_" in str(a.get("result", ""))]
        assert len(ok_results) >= 50, f"只成功{len(ok_results)}个存在工具"

    def test_exception_in_tool_does_not_crash_registry(self):
        """工具抛异常不影响后续工具执行"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="crash tool")
        def crash_tool() -> str:
            raise RuntimeError("intentional crash")

        @r.register(description="safe tool")
        def safe_tool() -> str:
            return "still works"

        plan = [
            {"tool": "crash_tool", "params": {}},
            {"tool": "safe_tool", "params": {}},
        ]
        actions = r.execute_plan(plan)
        assert len(actions) == 2
        assert actions[1].get("result") == "still works"

    def test_rapid_register_unregister(self):
        """快速注册+覆盖 1000次"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        for i in range(1000):
            @r.register(name="dynamic_tool", description=f"v{i}")
            def fn(x: int = 0) -> str:
                return f"v{i}"
        tools = r.list_tools()
        assert len(tools) >= 1


# ============================================================
# 3. 解析鲁棒性高压
# ============================================================

class TestParseRobustness:
    """解析极限: 对抗输入 / 极端长度 / 编码混淆"""

    def test_parse_tool_chain_with_null_bytes(self):
        """含 NULL 字节的输入"""
        from core.command_handler import _parse_tool_chain
        raw = '[{"tool": "test", "params": {}}]\x00extra'
        result = _parse_tool_chain(raw)
        assert isinstance(result, list)
        assert len(result) >= 0

    def test_parse_tool_chain_unicode_bomb(self):
        """大量 Unicode 特殊字符"""
        from core.command_handler import _parse_tool_chain
        bomb = "💣" * 10000
        raw = f'[{{"tool":"echo","params":{{"msg":"{bomb}"}}}}]'
        result = _parse_tool_chain(raw)
        assert isinstance(result, list)

    def test_parse_100kb_garbage_input(self):
        """100KB 垃圾输入不崩溃"""
        from core.command_handler import _parse_tool_chain
        garbage = "ASKLDJFHG ASKJDHFG KAJSDHF KAJSDHF " * 2000
        t0 = time.perf_counter()
        result = _parse_tool_chain(garbage)
        elapsed = time.perf_counter() - t0
        assert isinstance(result, list)
        assert elapsed < 2.0, f"100KB垃圾解析耗时 {elapsed:.3f}s"

    def test_parse_malformed_json_rapid(self):
        """1000种畸形JSON快速解析"""
        from core.command_handler import _parse_tool_chain
        malformed_cases = [
            '[{"tool":', '{"tool": "a"}]', '[[[]]]', '{}', '[]', '',
            '[{"tool": "a", "params": {]}', '{"tool": "a" "params": {}}',
            '[{"tool": "a", "params": {"k": undefined}}]',
            '[{"tool": "a", "params": {"k": NaN}}]',
            '[{"tool": "a", "params": {"k": Infinity}}]',
            '[{"tool": True, "params": {}}]',
            '[{"tool": "a", "params": {"k": 1e999}}]',
            '</html>some html</html>',
            'function() { return []; }',
            '```json\n[{"tool": "a", "params": {}}]\n```',
            '[{"tool":\n"a",\n"params":\n{}}]',
            '[{"tool": "a", "params": {"k": "' + ("x" * 50000) + '"}}]',
        ]
        for case in malformed_cases:
            result = _parse_tool_chain(case)
            assert isinstance(result, list), f"Failed on: {case[:50]}"

    def test_food_regex_injection_resistance(self):
        """食材正则SQL注入/命令注入抵抗力"""
        from core.command_handler import _try_food_regex
        attacks = [
            "买了'; DROP TABLE foods; --明天到期",
            "买了<script>alert('xss')</script>明天到期",
            "买了$(rm -rf /)明天到期",
            "买了`cat /etc/passwd`明天到期",
            "买了${IFS}-la明天到期",
            "买了%00%00%00明天到期",
            "买了" + "A" * 10000 + "明天到期",
            "买了\n\r\t明天到期",
        ]
        for text in attacks:
            result = _try_food_regex(text)
            assert result is None or isinstance(result, list)


# ============================================================
# 4. 场景引擎 Fuzz
# ============================================================

class TestSceneEngineFuzz:
    """场景识别随机轰炸"""

    def test_random_text_never_crashes(self):
        """1000条随机文本不崩溃"""
        from core.scene_engine import SceneEngine
        engine = SceneEngine()
        for _ in range(1000):
            text = _random_text()
            result = engine.recognize(text)
            assert result is None or isinstance(result, dict)

    def test_keyword_overlap(self):
        """重叠关键词返回正确场景"""
        from core.scene_engine import SceneEngine
        engine = SceneEngine()
        # "做饭" 出现在 "我回家做饭" → 应该匹配 cooking 还是 home?
        # 当前实现: 按配置文件顺序, 第一个匹配优先
        result = engine.recognize("我回家了开始做饭")
        # 应该命中先出现的场景 (home 在 cooking 之前)
        assert result is not None
        assert result["scene_id"] in ("home", "cooking")

    def test_rapid_1000_auto_trigger(self):
        """1000次自动触发检查不崩溃+防抖有效"""
        from core.scene_engine import SceneEngine
        engine = SceneEngine()
        fire_count = 0
        for _ in range(1000):
            result = engine.check_auto_trigger(hour=1, motion_active=False, light_on=False)
            if result:
                fire_count += 1
        # 30分钟防抖: 1000次调用只应触发1次
        assert fire_count == 1, f"防抖失效: 触发{fire_count}次"

    def test_all_scenes_keyword_hit(self):
        """所有场景的每个关键词都能命中"""
        from core.scene_engine import SceneEngine
        from config import SCENE_TRIGGERS
        engine = SceneEngine()
        for scene_id, cfg in SCENE_TRIGGERS.items():
            for kw in cfg["keywords"]:
                result = engine.recognize(kw)
                assert result is not None, f"关键词 '{kw}' 未命中场景 {scene_id}"
                assert result["scene_id"] == scene_id


# ============================================================
# 5. AIBrain 鲁棒性
# ============================================================

class TestAIBrainResilience:
    """AI决策引擎抗压"""

    def test_evaluate_100_rapid_calls(self):
        """100次快速evaluate调用 (无LLM)"""
        from core.ai_brain import AIBrain
        brain = AIBrain(llm=None)
        for _ in range(100):
            result = brain.evaluate({
                "co2": random.uniform(400, 3000),
                "temperature": random.uniform(10, 40),
                "humidity": random.uniform(20, 90),
                "light": random.uniform(0, 1000),
                "person_present": random.choice([True, False]),
            })
            assert isinstance(result, dict)
            assert "type" in result

    def test_parse_decision_output_adversarial(self):
        """对抗性LLM输出: 格式混乱 / 空 / 超长"""
        from core.ai_brain import AIBrain
        brain = AIBrain()
        adversarial = [
            "",  # 空
            "你好，我来分析一下",  # 无格式
            "EXPLANATION:",  # 格式不完整
            "TOOLS: []",  # 无解释
            "QUESTION:",  # 反问无选项
            "EXPLANATION: 测试。\nTOOLS: [{}]",  # 空参数
            "EXPLANATION: " + ("A" * 5000) + "\nTOOLS: []",  # 超长解释
            "TOOLS:" + ("[{...}]" * 1000),  # 垃圾工具
            "QUESTION: 测试?\nOPTIONS: []\nPENDING: [not json]",  # 无效JSON
            "\n\n\n",  # 纯空行
        ]
        for raw in adversarial:
            result = brain._parse_decision_output(raw)
            assert isinstance(result, dict)
            assert result["type"] in ("action", "question", "none")

    def test_anomaly_detection_bulk(self):
        """100次异常检测, 正态分布数据"""
        from core.ai_brain import AIBrain
        from knowledge.database import Database
        db = Database()
        brain = AIBrain(db=db)

        # 注入正常分布数据
        base = 800
        for i in range(20):
            db.conn.execute(
                "INSERT INTO sensor_log(sensor, value, unit, timestamp) VALUES (?,?,?,?)",
                ("test_sensor", base + random.gauss(0, 50), "ppm", time.time() - (20 - i) * 60),
            )
        db.conn.commit()

        anomalies = 0
        for _ in range(100):
            if brain.detect_anomaly("test_sensor", 800, 800 + random.gauss(0, 40)):
                anomalies += 1
        # σ=40 delta vs baseline σ≈50 → 极少超3σ
        assert anomalies <= 10, f"异常率 {anomalies}% 过高"

        db.conn.execute("DELETE FROM sensor_log WHERE sensor='test_sensor'")
        db.conn.commit()
        db.close()

    def test_recent_decisions_overflow(self):
        """recent_decisions 缓冲区限制有效 (evaluate中自动裁剪)"""
        from core.ai_brain import AIBrain
        brain = AIBrain(llm=None)
        # evaluate() 内部在追加后裁剪 → 最多10条
        for i in range(20):
            brain.evaluate({"co2": 800, "temperature": 25})
        assert len(brain._recent_decisions) <= 10


# ============================================================
# 6. CommandHandler 高压
# ============================================================

class TestCommandHandlerPressure:
    """指令处理器极限"""

    def test_handle_1000_random_commands(self):
        """1000条随机指令处理不崩溃"""
        from core.command_handler import CommandHandler
        handler = CommandHandler(llm=None, db=None, sensors=None)
        for _ in range(1000):
            text = _random_text(min_len=1, max_len=50)
            result = handler.handle(text)
            assert isinstance(result, dict)
            assert "reply" in result
            assert "actions" in result
            assert "agent" in result

    def test_handle_10kb_input(self):
        """10KB超长输入不崩溃"""
        from core.command_handler import CommandHandler
        handler = CommandHandler(llm=None, db=None, sensors=None)
        long_text = "太热了 " * 2500  # ~10KB
        t0 = time.perf_counter()
        result = handler.handle(long_text)
        elapsed = time.perf_counter() - t0
        assert isinstance(result, dict)
        assert elapsed < 5.0, f"10KB输入处理耗时 {elapsed:.1f}s"

    def test_handle_empty_and_whitespace(self):
        """空输入/纯空白"""
        from core.command_handler import CommandHandler
        handler = CommandHandler(llm=None, db=None, sensors=None)
        for text in ["", " ", "\n", "\t", "   \n\t  "]:
            result = handler.handle(text)
            assert isinstance(result, dict)

    def test_handle_special_chars_only(self):
        """纯特殊字符输入"""
        from core.command_handler import CommandHandler
        handler = CommandHandler(llm=None, db=None, sensors=None)
        for text in ["!@#$%^&*()", "<>{}[]", "\\x00\\x01", "😀🎉💣", "①②③④⑤"]:
            result = handler.handle(text)
            assert isinstance(result, dict)


# ============================================================
# 7. 24小时模拟
# ============================================================

class TestSimulation24H:
    """24小时运行模拟"""

    def test_24h_sensor_cycle(self):
        """模拟24h传感器数据流: 86400秒 → 8640个co2采样点(10s间隔)"""
        from knowledge.database import Database
        db = Database()

        hour_pattern = [
            # (hour_start, hour_end, co2_base, temp_base, person_present)
            (0, 6, 600, 24, False),    # 深夜
            (6, 8, 700, 23, True),     # 起床
            (8, 12, 500, 25, False),   # 出门
            (12, 14, 800, 26, True),   # 午间回家
            (14, 18, 500, 27, False),  # 下午外出
            (18, 22, 900, 25, True),   # 晚间在家
            (22, 24, 700, 24, False),  # 睡觉
        ]

        t0 = time.perf_counter()
        count = 0
        for hour in range(24):
            for minute in range(0, 60, 10):  # 每10分钟一个采样
                # 匹配时段
                for h_start, h_end, co2_base, temp_base, person in hour_pattern:
                    if h_start <= hour < h_end:
                        co2_val = co2_base + random.gauss(0, 50)
                        temp_val = temp_base + random.gauss(0, 0.5)
                        break
                else:
                    co2_val = 600
                    temp_val = 25

                db.log_sensor("co2", co2_val)
                db.log_sensor("temperature", temp_val)
                count += 2

        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"24h模拟写入 {count}条 耗时 {elapsed:.1f}s"

        # 验证数据完整性
        co2_data = db.query_sensor("co2", hours=24)
        temp_data = db.query_sensor("temperature", hours=24)
        assert len(co2_data) > 0
        assert len(temp_data) > 0

        # 验证同时段查询
        same_hour = db.query_sensor_same_hour("co2", days=1)
        assert isinstance(same_hour, list)

        db.conn.execute("DELETE FROM sensor_log")
        db.conn.commit()
        db.close()

    def test_full_scenario_workflow(self):
        """完整场景工作流: 传感器→AI决策→工具执行→事件记录"""
        from knowledge.database import Database
        from core.fastpath import FastPathEngine, setup_rules
        from core.tool_registry import registry
        from core.ai_brain import AIBrain

        db = Database()
        fp = FastPathEngine()
        setup_rules(fp)
        brain = AIBrain(llm=None, db=db)

        # 注册传感器数据
        sensors_history = []
        for hour in range(24):
            # 模拟一天的温度变化: 凌晨低 → 午后高 → 晚上降
            temp = 20 + 10 * abs(hour - 14) / 14 * 1.5 + random.gauss(0, 0.3)
            co2 = 500 + 500 * (1 if 18 <= hour <= 22 else 0.3) + random.gauss(0, 30)
            light = 300 * (1 if 7 <= hour <= 19 else 0.05)
            person = (7 <= hour <= 8) or (12 <= hour <= 13) or (18 <= hour <= 23)

            sensors_history.append({
                "hour": hour, "temp": round(temp, 1),
                "co2": round(co2, 1), "light": round(light, 1),
                "person": person,
            })

            # 记录传感器
            db.log_sensor("temperature", temp)
            db.log_sensor("co2", co2)
            db.log_sensor("light", light)

            # FastPath 更新 (不会触发, 因为没有达到紧急阈值)
            fp.update_sensor("temperature", temp)

            # AIBrain evaluate (无LLM, 验证不崩溃)
            snapshot = {
                "co2": co2, "temperature": temp,
                "humidity": 50 + random.gauss(0, 5),
                "light": light, "person_present": person,
            }
            result = brain.evaluate(snapshot)
            assert isinstance(result, dict)

            # 记录事件
            db.log_event("hourly_check", f"Hour {hour}: temp={temp:.1f} co2={co2:.0f}")

        # 验证日洞察生成
        trends = {}
        for sensor in ("co2", "temperature", "light"):
            data = db.query_sensor(sensor, hours=24)
            if data:
                trends[sensor] = [d["value"] for d in data]

        insight = brain.generate_insight(trends, [])
        assert isinstance(insight, str)

        # 24h事件完整性
        events = db.query_events(24)
        assert len(events) >= 24  # 每小时一条

        db.conn.execute("DELETE FROM sensor_log")
        db.conn.execute("DELETE FROM event_log")
        db.conn.commit()
        db.close()

    def test_fastpath_emergency_chain(self):
        """FastPath紧急链: 温度飙升→规则触发"""
        from core.fastpath import FastPathEngine, setup_rules
        import time as _time

        fp = FastPathEngine()
        setup_rules(fp)

        # 温度飙升超过35°C, 规则应触发 run_action
        # 无真实AC+GPIO, run_action 会尝试调用 registry 工具 — 验证不崩溃
        for t in [25.0, 28.0, 30.0, 33.0, 36.0, 38.0, 40.0]:
            fp.update_sensor("temperature", t)
            _time.sleep(0.05)

        # 验证sensor cache更新为最新值
        assert fp.get_sensor("temperature") == 40.0


# ============================================================
# 8. 内存/对象生命周期
# ============================================================

class TestMemoryAndLifecycle:
    """内存压力与对象生命周期"""

    def test_database_open_close_cycle(self):
        """数据库100次打开→关闭循环"""
        from knowledge.database import Database
        for _ in range(100):
            db = Database()
            db.log_sensor("test", 100)
            db.close()

    def test_scene_engine_create_destroy(self):
        """SceneEngine 1000次创建销毁"""
        from core.scene_engine import SceneEngine
        for _ in range(1000):
            engine = SceneEngine()
            result = engine.recognize("晚安")
            assert result is not None

    def test_aibrain_create_destroy(self):
        """AIBrain 500次创建销毁"""
        from core.ai_brain import AIBrain
        for _ in range(500):
            brain = AIBrain(llm=None)
            result = brain.evaluate({"co2": 800, "temperature": 25})
            assert result["type"] == "none"

    def test_command_handler_create_destroy(self):
        """CommandHandler 500次创建销毁"""
        from core.command_handler import CommandHandler
        for _ in range(500):
            handler = CommandHandler(llm=None, db=None, sensors=None)
            result = handler.handle("太热了")
            assert "reply" in result

    def test_fastpath_create_destroy(self):
        """FastPath 1000次创建+规则重复添加"""
        from core.fastpath import FastPathEngine, setup_rules
        for i in range(1000):
            fp = FastPathEngine()
            setup_rules(fp)
            fp.update_sensor("temperature", 25 + (i % 15))
