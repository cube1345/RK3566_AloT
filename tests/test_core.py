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
