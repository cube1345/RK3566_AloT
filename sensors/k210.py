# K210 摄像头传感器 — UART JSON 协议驱动
import json
import logging
import os
import time

from sensors.base import BaseSensor, SensorReading
from config import PLATFORM

try:
    import serial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False

logger = logging.getLogger("sensors.k210")

# K210 串口默认设备 (独立于 MH-Z19B 的 UART)
if PLATFORM == "pi5":
    _DEFAULT_K210_UART = "/dev/ttyAMA1"
elif PLATFORM == "rk3566":
    _DEFAULT_K210_UART = "/dev/ttyS3"
else:
    _DEFAULT_K210_UART = "/dev/ttyS1"

K210_UART = os.getenv("AGENT_K210_UART", _DEFAULT_K210_UART)
K210_BAUD = int(os.getenv("AGENT_K210_BAUD", "115200"))

# 事件类型
EVENT_ENTER = "enter"
EVENT_LEAVE = "leave"
EVENT_GESTURE = "gesture"
EVENT_ALERT = "alert"
EVENT_BARCODE = "barcode"
EVENT_OBJECT = "object"
EVENT_FACE_COUNT = "face_count"


class K210Sensor(BaseSensor):
    """K210 摄像头 — UART 串口读取结构化事件"""

    name = "k210"

    def __init__(self, device: str | None = None, baud: int = K210_BAUD):
        self._device = device or K210_UART
        self._baud = baud
        self._ser: "serial.Serial | None" = None
        self._last_event: dict = {}
        self._last_read_time: float = 0
        self._person_present: bool = False
        self._person_name: str = ""
        self._face_count: int = 0
        self._gesture: str = ""
        self._alert: dict = {}
        self._buffer: str = ""

    def read(self) -> SensorReading:
        now = time.time()
        self._last_read_time = now
        event = self._read_event()

        if event:
            self._last_event = event
            etype = event.get("event", "")

            if etype == EVENT_ENTER:
                self._person_present = True
                self._person_name = event.get("person", "unknown")
            elif etype == EVENT_LEAVE:
                self._person_present = False
                self._person_name = ""
            elif etype == EVENT_GESTURE:
                self._gesture = event.get("action", "")
            elif etype == EVENT_ALERT:
                self._alert = event
            elif etype == EVENT_FACE_COUNT:
                count = event.get("count", 0)
                self._face_count = count
                self._person_present = count > 0

        return SensorReading(
            value=1.0 if self._person_present else 0.0,
            unit="bool",
            timestamp=now,
            raw={
                "person_present": self._person_present,
                "person_name": self._person_name,
                "face_count": self._face_count,
                "gesture": self._gesture,
                "alert": self._alert,
                "last_event": self._last_event,
            },
        )

    def _read_event(self) -> dict | None:
        """非阻塞读取一行 JSON，返回解析后的事件 dict 或 None"""
        if not _HAS_SERIAL:
            return None
        try:
            ser = self._ensure_open()
            if not ser:
                return None
            # 非阻塞读取所有可用字节
            waiting = ser.in_waiting
            if waiting > 0:
                chunk = ser.read(waiting).decode("utf-8", errors="replace")
                self._buffer += chunk
                # 按换行分割
                lines = self._buffer.split("\n")
                self._buffer = lines[-1]  # 不完整的行留在 buffer
                for line in lines[:-1]:
                    line = line.strip()
                    if line:
                        try:
                            return json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug("K210 JSON 解析失败: %.80s", line)
        except (OSError, serial.SerialException) as e:
            logger.debug("K210 串口读取异常: %s", e)
            self._ser = None
        return None

    def _ensure_open(self):
        if not _HAS_SERIAL:
            return None
        if self._ser and self._ser.is_open:
            return self._ser
        try:
            self._ser = serial.Serial(
                self._device, self._baud,
                timeout=0.1,
                write_timeout=0.5,
            )
            logger.info("K210 串口已打开: %s @ %d", self._device, self._baud)
        except Exception as e:
            logger.debug("K210 串口打开失败: %s", e)
            return None
        return self._ser

    def send_command(self, cmd: str):
        """向 K210 发送指令"""
        if not _HAS_SERIAL:
            return
        try:
            ser = self._ensure_open()
            if ser:
                ser.write((cmd.strip() + "\n").encode("utf-8"))
                logger.debug("K210 ← %s", cmd)
        except Exception as e:
            logger.debug("K210 发送失败: %s", e)

    def send_lcd(self, text: str, color: int = 0xFFFF):
        """发送 LCD 显示内容 (K210 端解析)"""
        self.send_command(f"DISP:{text}:{color:04X}")

    @property
    def person_present(self) -> bool:
        return self._person_present

    @property
    def person_name(self) -> str:
        return self._person_name

    @property
    def gesture(self) -> str:
        g = self._gesture
        self._gesture = ""  # 读取后清除
        return g

    @property
    def alert(self) -> dict:
        a = self._alert
        self._alert = {}
        return a

    @property
    def last_event(self) -> dict:
        return self._last_event

    def cleanup(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            logger.info("K210 串口已关闭")


class MockK210Sensor(BaseSensor):
    """K210 Mock — 开发/测试用，不连真实硬件"""

    name = "k210"

    def read(self) -> SensorReading:
        return SensorReading(
            value=0.0,
            unit="bool",
            timestamp=time.time(),
            raw={
                "person_present": False,
                "person_name": "",
                "face_count": 0,
                "gesture": "",
                "alert": {},
                "last_event": {},
            },
        )

    @property
    def person_present(self) -> bool:
        return False

    @property
    def person_name(self) -> str:
        return ""

    @property
    def gesture(self) -> str:
        return ""

    @property
    def alert(self) -> dict:
        return {}

    @property
    def last_event(self) -> dict:
        return {}
