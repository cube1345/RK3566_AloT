# Agent 编排器 — 主循环
import asyncio
import logging
import time

from config import SENSOR_INTERVAL, AGENT, MOCK_SENSORS, LLM_MODEL
from core.fastpath import FastPathEngine, setup_rules
from core.tool_registry import registry
from sensors.base import SensorManager
from knowledge.database import Database

logger = logging.getLogger("agent")


class AgentOrchestrator:
    def __init__(self):
        self.fastpath = FastPathEngine()
        self.sensors = SensorManager()
        self.db = Database()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._llm_available = False

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

        # 3. 配置规则引擎
        setup_rules(self.fastpath)

        # 4. 尝试加载 LLM
        await self._init_llm()

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
        self.db.close()
        logger.info("Agent ZX 已停止")

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

        @registry.register()
        def read_person_present() -> bool:
            return self.sensors.read("motion").value > 0.5

        # 设备控制 (mock 实现)
        @registry.register(description="风扇控制 0关-3高速")
        def set_fan(speed: int):
            logger.info("执行: 风扇 → %s", ["关", "低速", "中速", "高速"][speed])
            return f"风扇: {['关', '低速', '中速', '高速'][speed]}"

        @registry.register(description="空调控制 mode=cool/dry/heat")
        def ac_control(mode: str, temp: int = 26, fan_speed: str = "auto"):
            logger.info("执行: 空调 → %s %d°C 风量%s", mode, temp, fan_speed)
            return f"空调: {mode} {temp}°C"

        @registry.register(description="灯光控制 on/off, brightness=0-255")
        def set_light(state: str, brightness: int = 255):
            logger.info("执行: 灯光 → %s 亮度%d", state, brightness)
            return f"灯光: {state}"

        @registry.register(description="空气净化 0关-3强")
        def set_air_purifier(level: int):
            logger.info("执行: 净化 → %s", ["关", "低速", "中速", "高速"][level])
            return f"净化: {['关', '低速', '中速', '高速'][level]}"

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

        # 通知
        @registry.register(description="TTS 语音播报")
        def tts(text: str):
            logger.info("TTS: %s", text)
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
            self.sensors.register(MockCO2())
            self.sensors.register(MockTempHumid())
            self.sensors.register(MockLight())
            self.sensors.register(MockMotion())
            logger.info("使用 MOCK 传感器")
        else:
            from sensors.co2 import CO2Sensor
            from sensors.temp_humid import TempHumidSensor
            from sensors.light import LightSensor
            from sensors.motion import MotionSensor
            self.sensors.register(CO2Sensor())
            self.sensors.register(TempHumidSensor())
            self.sensors.register(LightSensor())
            self.sensors.register(MotionSensor())
            logger.info("使用真实传感器")

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

    async def _poll_sensor(self, name: str, interval: int):
        while self._running:
            try:
                reading = self.sensors.read(name)
                # 记录到数据库
                self.db.log_sensor(name, reading.value, reading.unit)
                # 推送 FastPath 规则引擎
                self.fastpath.update_sensor(name, reading.value)
                # 处理湿度 (温湿度传感器的附加数据)
                if reading.raw and "humidity" in reading.raw:
                    self.fastpath.update_sensor("humidity", reading.raw["humidity"])
                    self.db.log_sensor("humidity", reading.raw["humidity"], "%")
            except Exception as e:
                logger.error("传感器 %s 读取失败: %s", name, e)
            await asyncio.sleep(interval)

    async def _doorbell_listener(self):
        """模拟外卖按键"""
        if MOCK_SENSORS:
            await asyncio.sleep(15)  # 启动后15s 模拟一次
            if self._running:
                logger.info("🔔 模拟: 外卖按键触发")
                tts("有客到")
                self.db.log_event("doorbell", "外卖按键触发")

        # 真实 GPIO 模式 (后续实现)
        # import gpiod
        # ...

    async def _scheduler(self):
        """定时任务调度"""
        while self._running:
            now = time.localtime()
            t = f"{now.tm_hour:02d}:{now.tm_min:02d}"

            if t == "08:00":
                await self._check_expiring_foods()
            elif t == "07:00":
                await self._daily_dress_advice()

            await asyncio.sleep(30)  # 每 30s 检查一次

    async def _check_expiring_foods(self):
        expiring = self.db.list_foods(expiring_days=1)
        if expiring:
            names = [f["name"] for f in expiring]
            msg = f"提醒: {', '.join(names)} 今天过期"
            tts(msg)
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
        tts(msg)
        self.db.log_event("dress_advice", msg)


from pathlib import Path
