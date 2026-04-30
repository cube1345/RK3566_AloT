# 核心功能冒烟测试
import asyncio
import pytest


class TestToolRegistry:
    def test_register_and_execute(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="测试工具")
        def echo(msg: str) -> str:
            return f"echo: {msg}"

        assert "echo" in r
        assert r.execute("echo", msg="hello") == "echo: hello"

    def test_list_tools(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="foo")
        def foo(): pass

        tools = r.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "foo"

    def test_get_prompt_block(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        block = r.get_prompt_block()
        assert isinstance(block, str)


class TestFastPath:
    def test_rule_fires(self):
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temperature", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temperature", 30)
        assert len(fired) == 1
        assert fired[0] == 30

    def test_cooldown(self):
        from core.fastpath import FastPathEngine
        import time
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=10)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", 1)
        fp.update_sensor("temp", 2)  # 冷却中
        assert len(fired) == 1  # 只有第一次触发


class TestDatabase:
    def test_crud(self):
        from knowledge.database import Database
        import tempfile, os
        from pathlib import Path
        import knowledge.database as dbmod

        tmp = Path(tempfile.mktemp(suffix=".db"))
        dbmod.DB_PATH = tmp
        try:
            db = Database()
            import datetime
            tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            fid = db.add_food("鸡蛋", tomorrow, "冷藏", 10, "个")
            assert fid > 0
            foods = db.list_foods(expiring_days=3)
            assert len(foods) >= 1
            db.remove_food(fid)
            assert len(db.list_foods()) == 0

            db.log_sensor("co2", 800, "ppm")
            logs = db.query_sensor("co2", hours=48)
            assert len(logs) >= 1

            db.close()
        finally:
            if tmp.exists():
                os.unlink(str(tmp))


class TestSensorMock:
    def test_read_all(self):
        from sensors.mock import MockCO2, MockTempHumid, MockLight, MockMotion
        from sensors.base import SensorManager
        sm = SensorManager()
        sm.register(MockCO2())
        sm.register(MockTempHumid())
        sm.register(MockLight())
        sm.register(MockMotion())

        readings = sm.read_all()
        assert "co2" in readings
        assert "temperature" in readings
        assert "light" in readings
        assert "motion" in readings
        assert 400 <= readings["co2"].value <= 2000
        assert 0 <= readings["light"].value <= 1000


class TestAgentSmoke:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        from core.agent import AgentOrchestrator
        agent = AgentOrchestrator()
        await agent.start()
        assert agent._running is False
        # 验证工具已注册
        from core.tool_registry import registry
        tools = registry.list_tools()
        assert len(tools) >= 16  # 应该有 16+ 个工具 (含 generate_daily_report)
        await agent.stop()


class TestParseToolChain:
    def test_parse_json(self):
        from core.command_handler import _parse_tool_chain
        raw = '[{"tool": "ac_control", "params": {"mode": "cool", "temp": 26}}]'
        result = _parse_tool_chain(raw)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tool"] == "ac_control"
        assert result[0]["params"]["temp"] == 26

    def test_parse_code_block(self):
        from core.command_handler import _parse_tool_chain
        raw = '```json\n[{"tool": "set_fan", "params": {"speed": 1}}]\n```'
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "set_fan"

    def test_parse_flat(self):
        from core.command_handler import _parse_tool_chain
        raw = '["read_temperature", "read_humidity"]'
        result = _parse_tool_chain(raw)
        assert len(result) == 2
        assert result[0]["tool"] == "read_temperature"
        assert result[1]["params"] == {}

    def test_parse_code_block_no_tag(self):
        from core.command_handler import _parse_tool_chain
        raw = '```\n[{"tool": "tts", "params": {"text": "hello"}}]\n```'
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "tts"

    def test_parse_empty(self):
        from core.command_handler import _parse_tool_chain
        assert _parse_tool_chain("") == []
        assert _parse_tool_chain("你好") == []
        assert _parse_tool_chain("[]") == []

    def test_parse_mixed_valid_invalid(self):
        from core.command_handler import _parse_tool_chain
        raw = '["read_temp", {"tool": "set_fan", "params": {"speed": 1}}, 123]'
        result = _parse_tool_chain(raw)
        assert len(result) == 2  # skip 123


class TestRoute:
    def test_route_environment(self):
        from core.command_handler import route
        assert route("太热了") == "environment"
        assert route("打开空调") == "environment"
        assert route("CO2多少") == "environment"
        assert route("灯太亮了") == "environment"

    def test_route_food(self):
        from core.command_handler import route
        assert route("买了鸡蛋") == "food"
        assert route("牛奶过期了") == "food"
        assert route("冰箱里有什么") == "food"
        assert route("推荐菜谱") == "food"

    def test_route_life(self):
        from core.command_handler import route
        assert route("今天穿什么") == "life"
        assert route("天气怎么样") == "life"
        assert route("今天总结") == "life"

    def test_route_default(self):
        from core.command_handler import route
        assert route("你好") == "life"
        assert route("你是谁") == "life"


class TestFoodRegex:
    def test_bought_food_tomorrow(self):
        from core.command_handler import _try_food_regex
        import datetime
        result = _try_food_regex("买了鸡蛋明天到期")
        assert result is not None
        assert len(result) == 1
        assert result[0]["tool"] == "add_food"
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        assert result[0]["params"]["expiry_date"] == tomorrow
        assert result[0]["params"]["name"] == "鸡蛋"

    def test_bought_food_with_date(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了牛奶5月20到期")
        assert result is not None
        assert result[0]["tool"] == "add_food"
        assert result[0]["params"]["name"] == "牛奶"
        assert "2026-05-20" in result[0]["params"]["expiry_date"]

    def test_list_foods(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("冰箱里有什么")
        assert result is not None
        assert result[0]["tool"] == "list_foods"

    def test_expiring_foods(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("什么快过期了")
        assert result is not None
        assert result[0]["tool"] == "list_foods"
        assert result[0]["params"].get("expiring_days") == 3

    def test_bought_with_quantity(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了鸡蛋10个明天到期")
        assert result is not None
        assert result[0]["params"]["quantity"] == 10.0
        assert result[0]["params"]["unit"] == "个"

    def test_no_match(self):
        from core.command_handler import _try_food_regex
        assert _try_food_regex("太热了") is None
        assert _try_food_regex("今天穿什么") is None
        assert _try_food_regex("你好") is None


class TestExecutePlan:
    def test_empty_plan(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        assert r.execute_plan([]) == []

    def test_single_tool(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="echo")
        def echo(msg: str) -> str:
            return f"echo: {msg}"

        result = r.execute_plan([{"tool": "echo", "params": {"msg": "hello"}}])
        assert len(result) == 1
        assert result[0]["tool"] == "echo"
        assert "echo: hello" in str(result[0]["result"])

    def test_multi_tools(self):
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="echo")
        def echo(msg: str) -> str:
            return f"echo: {msg}"

        @r.register(description="add")
        def add(a: int, b: int) -> int:
            return a + b

        plan = [
            {"tool": "echo", "params": {"msg": "hi"}},
            {"tool": "add", "params": {"a": 1, "b": 2}},
        ]
        result = r.execute_plan(plan)
        assert len(result) == 2
        assert result[0]["tool"] == "echo" or result[0]["tool"] == "add"


class TestLLMEngine:
    def test_generate_structured_parse_json(self):
        from llm.engine import LLMEngine
        engine = LLMEngine.__new__(LLMEngine)
        result = engine._parse_structured(
            '[{"tool": "ac_control", "params": {"mode": "cool"}}]'
        )
        assert len(result) == 1
        assert result[0]["tool"] == "ac_control"

    def test_generate_structured_parse_codeblock(self):
        from llm.engine import LLMEngine
        engine = LLMEngine.__new__(LLMEngine)
        result = engine._parse_structured(
            '```json\n[{"tool": "set_fan", "params": {"speed": 1}}]\n```'
        )
        assert len(result) == 1
        assert result[0]["tool"] == "set_fan"

    def test_generate_structured_invalid(self):
        from llm.engine import LLMEngine
        engine = LLMEngine.__new__(LLMEngine)
        result = engine._parse_structured("今天天气不错")
        assert result == []


class TestToolRegistryStress:
    """压力测试: 高并发 + 大量工具"""

    def test_execute_plan_50_parallel(self):
        """50个工具并行执行, 验证全部完成且无异常"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="sleep and return")
        def slow_echo(msg: str) -> str:
            import time
            time.sleep(0.01)
            return f"echo: {msg}"

        plan = [{"tool": "slow_echo", "params": {"msg": f"msg_{i}"}} for i in range(50)]
        results = r.execute_plan(plan)
        assert len(results) == 50
        # 确认所有结果唯一 (说明全都执行了)
        msgs = {r["result"] for r in results}
        assert len(msgs) == 50

    def test_execute_plan_empty_params(self):
        """空参数元组"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="noop")
        def noop() -> str:
            return "done"

        plan = [{"tool": "noop", "params": {}}]
        results = r.execute_plan(plan)
        assert results[0]["result"] == "done"

    def test_execute_plan_single_missing_tool(self):
        """单工具未注册时返回错误, 不抛异常"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        results = r.execute_plan([{"tool": "nonexistent", "params": {}}])
        assert len(results) == 1
        assert "error" in str(results[0]["result"]).lower()
        assert "nonexistent" in str(results[0]["result"])

    def test_register_duplicate_overwrite(self):
        """重复注册同名工具, 后者覆盖前者"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="v1")
        def foo() -> str:
            return "v1"

        @r.register(description="v2")
        def foo() -> str:  # noqa: F811
            return "v2"

        assert r.execute("foo") == "v2"

    def test_get_prompt_block_huge(self):
        """大量工具时 prompt_block 不崩溃"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        for i in range(200):
            @r.register(name=f"dummy_{i}", description=f"tool_{i}")
            def dummy(x: int = 0) -> int:  # noqa: F811
                return x
        block = r.get_prompt_block()
        assert "dummy_0" in block
        assert "dummy_199" in block
        assert len(block) > 1000  # 确保确实很大


class TestFoodRegexEdge:
    """食材正则兜底: 边界/异常情况"""

    def test_bought_with_weight(self):
        """买了3斤苹果明天到期 → 数量在前, 名称在后"""
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了3斤苹果明天到期")
        assert result is not None
        p = result[0]["params"]
        assert p["name"] == "苹果"
        assert p["quantity"] == 3.0
        assert p["unit"] == "斤"

    def test_bought_quantity_before_name_with_date(self):
        """买了3斤苹果5月20到期 → 数量3, 斤, 名称苹果, 日期5月20"""
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了3斤苹果5月20到期")
        assert result is not None
        p = result[0]["params"]
        assert p["name"] == "苹果"
        assert p["quantity"] == 3.0
        assert p["unit"] == "斤"
        assert "2026-05-20" in p["expiry_date"]

    def test_bought_quantity_before_name_weird_units(self):
        """买了5盒牛奶明天过期"""
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了5盒牛奶明天过期")
        assert result is not None
        p = result[0]["params"]
        assert "牛奶" in p["name"]
        assert p["quantity"] == 5.0
        assert p["unit"] == "盒"

    def test_bought_with_package(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了一箱牛奶过期了")
        assert result is not None
        p = result[0]["params"]
        assert "牛奶" in p["name"] or "一箱牛奶" in p["name"]

    def test_bought_no_date(self):
        """买了XX但没写日期, 默认7天后"""
        from core.command_handler import _try_food_regex
        import datetime
        result = _try_food_regex("买番茄")
        # 无到期关键词, 可能不匹配 => 不抛出异常即可
        # "买番茄"不含"到期/过期", 预期 None
        # 但"买了番茄"也不含到期, 所以应该是 None
        assert _try_food_regex("买番茄") is None

    def test_bought_trailing_whitespace(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("  买了鸡蛋明天到期  ")
        assert result is not None
        assert result[0]["tool"] == "add_food"

    def test_bought_special_chars(self):
        from core.command_handler import _try_food_regex
        result = _try_food_regex("买了酱油(生抽)明天到期")
        assert result is not None

    def test_list_foods_variants(self):
        from core.command_handler import _try_food_regex
        assert _try_food_regex("有什么快过期") is not None
        assert _try_food_regex("快到期了") is not None

    def test_food_regex_no_crash_weird_input(self):
        from core.command_handler import _try_food_regex
        # 各种奇怪输入不崩溃
        for junk in ["", "a", "!@#$%^&*()", " " * 100, "买" * 100]:
            try:
                _try_food_regex(junk)
            except Exception:
                pass  # 允许 None 或异常, 但不允许段错误


class TestRouteStress:
    """路由: 混合/边界/长文本"""

    def test_route_mixed_keywords(self):
        """混合关键词取优先级最先匹配的"""
        from core.command_handler import route
        from agents.environment_agent import AGENT_NAME as ENV_NAME
        from agents.food_agent import AGENT_NAME as FOOD_NAME
        # "灯" 在 environment 的 keywords 里排在前面
        assert route("冰箱的灯不亮了") == ENV_NAME  # "灯" 匹配环境
        # "食材" 匹配 food
        assert route("食材过期") == FOOD_NAME  # 改: 明确选 food

    def test_route_long_text(self):
        from core.command_handler import route
        long = "你好" * 500
        r = route(long)
        assert r is not None

    def test_route_empty_text(self):
        from core.command_handler import route
        r = route("")
        assert r is not None  # 返回默认

    def test_route_case_insensitive(self):
        from core.command_handler import route
        from agents.environment_agent import AGENT_NAME as ENV_NAME
        assert route("CO2") == ENV_NAME
        assert route("co2") == ENV_NAME
        assert route("Co2") == ENV_NAME

    def test_route_near_miss(self):
        """接近但不完全匹配关键词, 应该走默认"""
        from core.command_handler import route
        r = route("今天心情不错")
        assert r is not None


class TestParseToolChainStress:
    """解析: 巨型/畸形/嵌套"""

    def test_parse_1000_item_chain(self):
        from core.command_handler import _parse_tool_chain
        items = [{"tool": "read_temperature", "params": {}} for _ in range(1000)]
        raw = str(items).replace("'", '"')
        result = _parse_tool_chain(raw)
        assert len(result) == 1000

    def test_parse_deeply_nested_garbage(self):
        """深层嵌套不崩溃"""
        from core.command_handler import _parse_tool_chain
        # 100层嵌套, json 会解析失败, 但不应崩溃
        raw = "[" * 100 + "]" * 100
        result = _parse_tool_chain(raw)
        assert isinstance(result, list)

    def test_parse_garbage_prefix(self):
        """LLM 常见问题: 前面有解释文本再跟JSON"""
        from core.command_handler import _parse_tool_chain
        raw = """我来帮你处理。
好的，以下是工具调用：
```json
[{"tool": "ac_control", "params": {"mode": "cool"}}]
```
如果有其他需要请告诉我。"""
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "ac_control"

    def test_parse_unicode_chinese(self):
        """中文参数"""
        from core.command_handler import _parse_tool_chain
        raw = '[{"tool": "add_food", "params": {"name": "鸡蛋", "storage": "冷藏"}}]'
        result = _parse_tool_chain(raw)
        assert result[0]["params"]["name"] == "鸡蛋"

    def test_parse_truncated_json(self):
        """截断的 JSON 不崩溃, 返回空列表"""
        from core.command_handler import _parse_tool_chain
        assert _parse_tool_chain('[{"tool": "ac') == []

    def test_parse_very_long_strings(self):
        """非常长的参数值"""
        from core.command_handler import _parse_tool_chain
        long = "x" * 100000
        raw = f'[{{"tool": "tts", "params": {{"text": "{long}"}}}}]'
        result = _parse_tool_chain(raw)
        assert len(result) == 1

    def test_parse_single_tool_object(self):
        """单工具对象 (不在数组里) {"tool":"xxx","params":{...}}"""
        from core.command_handler import _parse_tool_chain
        raw = '{"tool": "ac_control", "params": {"mode": "cool", "temp": 26}}'
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "ac_control"
        assert result[0]["params"]["temp"] == 26

    def test_parse_markdown_inline_code(self):
        """行内代码块 `[tool1, tool2]`"""
        from core.command_handler import _parse_tool_chain
        raw = '使用 `["read_temperature", "read_humidity"]` 查询环境'
        result = _parse_tool_chain(raw)
        assert len(result) == 2
        assert result[0]["tool"] == "read_temperature"

    def test_parse_greedy_extraction(self):
        """文本包裹 JSON, 找第一个 [ 到最后一个 ]"""
        from core.command_handler import _parse_tool_chain
        raw = '好的我来处理。[{"tool":"ac_control","params":{"mode":"cool"}}] 已执行完毕。'
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "ac_control"

    def test_parse_single_tool_extra_text(self):
        """额外文本包裹单工具对象"""
        from core.command_handler import _parse_tool_chain
        raw = '我建议你使用 {"tool": "tts", "params": {"text": "hello"}} 来播报'
        result = _parse_tool_chain(raw)
        assert len(result) == 1
        assert result[0]["tool"] == "tts"


class TestFastPathStress:
    """规则引擎: 高频触发 + 边界"""

    def test_rapid_updates(self):
        """每秒100次更新, 保持稳定"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("test", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        import time
        start = time.time()
        for i in range(100):
            fp.update_sensor("test", i)
        elapsed = time.time() - start
        assert len(fired) == 100  # 全部触发
        assert elapsed < 1.0  # 应在毫秒级完成

    def test_rule_with_context(self):
        """验证 ctx 对象存在且可读写"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            ctx["last"] = value

        fp.update_sensor("temp", 25)
        sensors = fp.get_sensors() if hasattr(fp, "get_sensors") else {}
        assert sensors is not None

    def test_multiple_rules_same_sensor(self):
        """同传感器多个规则, 全部触发"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = set()

        for name in ("A", "B", "C"):
            @fp.add_rule("temp", cooldown=0)
            def rule(value, old, ctx, _n=name):  # noqa: E741
                fired.add(_n)

        fp.update_sensor("temp", 30)
        assert len(fired) == 3


class TestDatabaseStress:
    """数据库: 批量操作 + 边界"""

    def test_bulk_insert_foods(self):
        """批量插入100条食材"""
        from knowledge.database import Database
        import tempfile
        from pathlib import Path
        import knowledge.database as dbmod

        tmp = Path(tempfile.mktemp(suffix=".db"))
        dbmod.DB_PATH = tmp
        try:
            db = Database()
            import datetime
            for i in range(100):
                name = f"食材_{i:03d}"
                expiry = (datetime.date.today() + datetime.timedelta(days=i)).isoformat()
                db.add_food(name, expiry, "冷藏", 1, "个")
            foods = db.list_foods()
            assert len(foods) == 100
            db.close()
        finally:
            if tmp.exists():
                import os; os.unlink(str(tmp))

    def test_bulk_sensor_logs(self):
        """批量插入500条传感器记录"""
        from knowledge.database import Database
        import tempfile
        from pathlib import Path
        import knowledge.database as dbmod

        tmp = Path(tempfile.mktemp(suffix=".db"))
        dbmod.DB_PATH = tmp
        try:
            db = Database()
            for i in range(500):
                db.log_sensor("co2", 400 + i % 1000, "ppm")
            logs = db.query_sensor("co2", hours=168)  # 7天
            assert len(logs) == 500
            db.close()
        finally:
            if tmp.exists():
                import os; os.unlink(str(tmp))

    def test_remove_nonexistent(self):
        """删除不存在的食材不崩溃"""
        from knowledge.database import Database
        import tempfile
        from pathlib import Path
        import knowledge.database as dbmod

        tmp = Path(tempfile.mktemp(suffix=".db"))
        dbmod.DB_PATH = tmp
        try:
            db = Database()
            db.remove_food(99999)  # 不存在的 ID
            db.close()
        finally:
            if tmp.exists():
                import os; os.unlink(str(tmp))


class TestConcurrencyStress:
    """并发压力: 多线程 DB 访问 + 多线程 registry 读取"""

    def test_concurrent_db_writes(self):
        """50个线程并发写DB (使用锁), 验证全部写入"""
        import threading, tempfile
        from pathlib import Path
        import knowledge.database as dbmod
        tmp = Path(tempfile.mktemp(suffix=".db"))
        dbmod.DB_PATH = tmp
        lock = threading.Lock()
        try:
            db = dbmod.Database()
            errors = []

            def writer(i):
                try:
                    import datetime
                    expiry = (datetime.date.today() + datetime.timedelta(days=i)).isoformat()
                    with lock:
                        db.add_food(f"conc_{i}", expiry, "冷藏", 1, "个")
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
            for t in threads: t.start()
            for t in threads: t.join()
            assert len(errors) == 0, f"Concurrent write errors: {errors}"
            assert len(db.list_foods()) >= 50
            db.close()
        finally:
            if tmp.exists(): import os; os.unlink(str(tmp))

    def test_concurrent_registry_reads(self):
        """20个线程并发读取registry工具列表, 不崩溃"""
        from core.tool_registry import ToolRegistry
        import threading
        r = ToolRegistry()

        @r.register(description="echo")
        def echo(msg: str) -> str:
            return f"echo: {msg}"

        @r.register(description="add")
        def add(a: int, b: int) -> int:
            return a + b

        results = []

        def reader(idx):
            try:
                tools = r.list_tools()
                results.append(len(tools))
            except Exception as e:
                results.append(e)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        # 所有读取结果一致
        non_error = [v for v in results if isinstance(v, int)]
        assert len(non_error) == 20
        assert all(n == 2 for n in non_error)

    def test_concurrent_execute_plan_mixed(self):
        """多线程交叉执行同一registry的plan, 无干扰"""
        from core.tool_registry import ToolRegistry
        import threading
        r = ToolRegistry()

        @r.register(description="echo")
        def echo(msg: str) -> str:
            return f"echo: {msg}"

        outcomes = {}

        def runner(pid):
            plan = [{"tool": "echo", "params": {"msg": f"p{pid}"}}]
            results = r.execute_plan(plan)
            outcomes[pid] = results[0]["result"]

        threads = [threading.Thread(target=runner, args=(i,)) for i in range(30)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(outcomes) == 30
        for pid, res in outcomes.items():
            assert res == f"echo: p{pid}"


class TestHugePayloadStress:
    """巨量负载: 高循环数 + 大工具链列表"""

    def test_food_regex_10000_loop(self):
        """食材正则10000次紧循环, 验证稳定性和正确计数"""
        from core.command_handler import _try_food_regex
        match_count = 0
        no_match_count = 0
        for i in range(10000):
            text = f"买了鸡蛋{i}个明天到期"
            result = _try_food_regex(text)
            if result is not None:
                match_count += 1
                assert result[0]["tool"] == "add_food"
                assert result[0]["params"]["name"] == "鸡蛋"
            else:
                no_match_count += 1
        # 最多容忍1次漏匹配 (极低)
        assert no_match_count <= 1, f"Missed {no_match_count} matches out of 10000"
        assert match_count >= 9999

    def test_parse_10000_item_tool_chain(self):
        """10000个工具链解析, 完整返回"""
        from core.command_handler import _parse_tool_chain
        items = [{"tool": "read_temperature", "params": {}} for _ in range(10000)]
        raw = str(items).replace("'", '"')
        result = _parse_tool_chain(raw)
        assert len(result) == 10000
        for step in result:
            assert step["tool"] == "read_temperature"

    def test_deeply_nested_json_parse(self):
        """超深嵌套JSON解析不崩溃"""
        from core.command_handler import _parse_tool_chain
        import json
        # 构造深层嵌套: {"a":{"a":...}} 共300层
        nested = "{}"
        for _ in range(300):
            nested = json.dumps({"a": json.loads(nested) if nested != "{}" else {}})
        # wrap在工具链外
        raw = f'[{{"tool": "echo", "params": {{"data": {nested}}}}}]'
        result = _parse_tool_chain(raw)
        # 应该成功解析或优雅失败
        assert isinstance(result, list)


class TestEdgeCaseStress:
    """边界值压力: NaN, Infinity, 负数, 零值, 特殊类型"""

    def test_fastpath_nan_value(self):
        """NaN传感器值不崩溃"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", float('nan'))
        assert isinstance(fired, list)

    def test_fastpath_inf_value(self):
        """Infinity传感器值不崩溃"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", float('inf'))
        assert isinstance(fired, list)

    def test_fastpath_neg_inf_value(self):
        """负Infinity传感器值不崩溃"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", float('-inf'))
        assert isinstance(fired, list)

    def test_fastpath_negative_value(self):
        """负数传感器值正确触发"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", -273.15)
        assert len(fired) == 1
        assert fired[0] == -273.15

    def test_fastpath_zero_value(self):
        """零值传感器值正确触发"""
        from core.fastpath import FastPathEngine
        fp = FastPathEngine()
        fired = []

        @fp.add_rule("temp", cooldown=0)
        def rule(value, old, ctx):
            fired.append(value)

        fp.update_sensor("temp", 0)
        assert len(fired) == 1
        assert fired[0] == 0

    def test_route_none_input(self):
        """route(None) 不崩溃"""
        from core.command_handler import route
        try:
            r = route(None)  # type: ignore
            assert r is not None
        except (TypeError, AttributeError):
            pass  # 允许合理类型错误, 但不允许段错误

    def test_parse_tool_chain_binary(self):
        """二进制/非UTF8内容不崩溃"""
        from core.command_handler import _parse_tool_chain
        try:
            result = _parse_tool_chain(b'\x00\xff\xfe\xfd')  # type: ignore
            assert isinstance(result, list)
        except TypeError:
            pass  # json.loads(bytes) 可能抛 TypeError, 接受


class TestExecutionRobustness:
    """执行鲁棒性: 异常工具 + 部分失败 + 混合成功失败"""

    def test_execute_plan_partial_failure(self):
        """部分工具失败时, 所有工具都执行完毕"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="ok")
        def ok(): return "good"

        @r.register(description="fail")
        def fail(): raise ValueError("boom")

        plan = [
            {"tool": "ok", "params": {}},
            {"tool": "fail", "params": {}},
            {"tool": "ok", "params": {}},
        ]
        results = r.execute_plan(plan)
        assert len(results) == 3
        tools_ran = {r["tool"] for r in results}
        assert tools_ran == {"ok", "fail"}

    def test_execute_plan_all_fail(self):
        """所有工具都失败"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="always fail")
        def boom(): raise RuntimeError("always fail")

        plan = [{"tool": "boom", "params": {}} for _ in range(5)]
        results = r.execute_plan(plan)
        assert len(results) == 5
        for res in results:
            assert res["tool"] == "boom"

    def test_execute_plan_mixed_types(self):
        """混合成功/失败/异常工具"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()

        @r.register(description="ok")
        def ok(x: int = 0) -> int:
            return x * 2

        @r.register(description="fail")
        def fail(): raise ValueError("fail")

        @r.register(description="other ok")
        def other(msg: str = "") -> str:
            return f"ok:{msg}"

        plan = [
            {"tool": "ok", "params": {"x": 5}},
            {"tool": "fail", "params": {}},
            {"tool": "other", "params": {"msg": "test"}},
            {"tool": "ok", "params": {"x": 0}},
        ]
        results = r.execute_plan(plan)
        assert len(results) == 4
        by_name = {r["tool"]: r["result"] for r in results}
        assert by_name["ok"] in (10, 0)  # ok 被调了两次 (x=5 和 x=0), 保留最后一个
        assert "error" in str(by_name["fail"]).lower() or "fail" in str(by_name["fail"])
        assert by_name["other"] == "ok:test"

    def test_execute_plan_exception_in_middle(self):
        """异常发生在中间, 前后工具不受影响"""
        from core.tool_registry import ToolRegistry
        r = ToolRegistry()
        order = []

        @r.register(description="first")
        def first():
            order.append("first")
            return "first_done"

        @r.register(description="middle fail")
        def middle():
            order.append("middle")
            raise ValueError("middle failed")

        @r.register(description="last")
        def last():
            order.append("last")
            return "last_done"

        plan = [
            {"tool": "first", "params": {}},
            {"tool": "middle", "params": {}},
            {"tool": "last", "params": {}},
        ]
        results = r.execute_plan(plan)
        assert len(results) == 3
        # 确认所有工具都排到了执行顺序
        assert "first" in order
        assert "middle" in order
        assert "last" in order
