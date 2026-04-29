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
        assert len(tools) >= 15  # 应该有 15+ 个工具
        await agent.stop()
