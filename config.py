# 配置文件
import os
from pathlib import Path


ROOT = Path(__file__).parent

# ===== 运行模式 =====
MOCK_SENSORS = os.getenv("AGENT_MOCK", "1") == "1"  # 开发用 mock, 部署改 0
MOCK_WEATHER = os.getenv("AGENT_MOCK_WEATHER", "1") == "1"

# ===== 传感器 =====
SENSOR_INTERVAL = {
    "co2": 10,       # CO₂ 轮询间隔(秒)
    "temperature": 5, # 温湿度轮询间隔
    "light": 3,       # 光照轮询间隔
    "motion": 1,      # 人体红外轮询间隔
}

# ===== 传感器型号选择 (MOCK_SENSORS=0 时生效) =====
# CO₂: "mhz19b" (UART) / "sgp30" (I2C)
SENSOR_CO2 = os.getenv("AGENT_SENSOR_CO2", "mhz19b").lower()
# 温湿度: "sht30" (I2C) / "dht11" (GPIO)
SENSOR_TEMP = os.getenv("AGENT_SENSOR_TEMP", "sht30").lower()

# ===== 自动控制阈值 =====
CO2_THRESHOLDS = {
    "normal": 800,
    "warning": 1200,
    "critical": 2000,
}

TEMP_COMFORT = {
    "max": 28,
    "min": 18,
    "ac_cool_temp": 26,
    "ac_heat_temp": 22,
}

HUMIDITY_COMFORT = {
    "max": 80,
    "min": 35,
}

LIGHT_THRESHOLD = {
    "dim": 50,      # lux 以下开灯
    "dark": 20,     # lux 以下全亮
}

# ===== 平台选择 =====
# 可选: "pi5" (树莓派5B), "rk3566" (RK3566), "rk3506" (RK3506)
PLATFORM = os.getenv("AGENT_PLATFORM", "pi5").lower()

# ===== GPIO 引脚定义 =====
# 所有值为 BCM GPIO number (非物理引脚号)
if PLATFORM == "pi5":
    # 树莓派 5 (BCM2712) — 40-pin Header 标准引脚
    # 对照: https://pinout.xyz/
    GPIO = {
        "relay_light": 17,      # 物理引脚 11
        "relay_fan": 27,        # 物理引脚 13
        "ir_led": 22,           # 物理引脚 15 — 空调红外发射
        "led_green": 23,        # 物理引脚 16 — 绿色指示灯
        "led_red": 24,          # 物理引脚 18 — 红色指示灯
        "relay_purifier": 10,   # 物理引脚 19 — 净化器继电器
        "doorbell_btn": 9,      # 物理引脚 21 — 门铃按键输入
        "motion_sensor": 25,    # 物理引脚 22 — HC-SR501 输入
        "dht11": 4,             # 物理引脚 7  — DHT11 数据线
    }
    GPIO_CHIP = "/dev/gpiochip0"
elif PLATFORM in ("rk3566", "rk3506"):
    # RK3566 / RK3506 — GPIO bank: GPIO0=0-31, GPIO1=32-63 ...
    # 需根据实际 PCB 走线确认
    GPIO = {
        "led_green": 23,
        "led_red": 24,
        "relay_light": 11,
        "relay_fan": 13,
        "relay_purifier": 19,
        "ir_led": 15,
        "doorbell_btn": 21,
        "motion_sensor": 7,
    }
    GPIO_CHIP = os.getenv("AGENT_GPIO_CHIP", "/dev/gpiochip0")
else:
    GPIO = {}
    GPIO_CHIP = "/dev/gpiochip0"

# ===== I2C 总线 =====
# Pi 5:  /dev/i2c-1, bus=1 (物理引脚 3=SDA, 5=SCL)
# RK3566: /dev/i2c-1, bus=1
# 查看:  ls /dev/i2c-* && i2cdetect -l
I2C_BUS = int(os.getenv("AGENT_I2C_BUS", "1"))
I2C_ADDR = {
    "sht30": 0x44,   # SHT30 默认地址 (ADDR=L)
    "bh1750": 0x23,  # BH1750 默认地址 (ADDR=L)
}

# ===== UART (MH-Z19B) =====
# Pi 5:  启用 UART 后 → /dev/ttyAMA0 (需在 config.txt 添加 enable_uart=1)
# RK3566: uart2 → /dev/ttyS2 (引脚 8=TX, 10=RX)
# RK3506: 取决于设备树, 常见 /dev/ttyS0
if PLATFORM == "pi5":
    _DEFAULT_UART = "/dev/ttyAMA0"
elif PLATFORM == "rk3566":
    _DEFAULT_UART = "/dev/ttyS2"
else:
    _DEFAULT_UART = "/dev/ttyS0"
UART_DEV = os.getenv("AGENT_UART", _DEFAULT_UART)
UART_BAUD = 9600

# ===== LLM =====
LLM_MODEL = os.getenv("AGENT_LLM", str(ROOT / "models/Qwen2.5-1.5B-Instruct.Q4_K_M.gguf"))
LLM_CTX_SIZE = 4096
LLM_MAX_TOKENS = 512
LLM_TEMPERATURE = 0.0

# ===== Agent =====
AGENT = {
    "max_history_rounds": 3,           # 上下文保留最近几轮
    "midpath_llm_timeout": 3.0,        # MidPath LLM 异步超时 (秒)
    "sensor_history_retention_days": 30,
    "food_check_time": "08:00",        # 食材过期检查时间
    "dress_advice_time": "07:00",      # 穿衣建议推送时间
}

# ===== Database =====
DB_PATH = ROOT / "data" / "agent.db"

# ===== Web =====
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

# ===== GPIO 可用 (非 mock 时) =====
GPIO_AVAILABLE = os.getenv("AGENT_GPIO", "0") == "1"

# ===== 继电器触发极性 =====
# 大多数国产模块是 LOW 触发 (IN=LOW → 继电器吸合)
# 少数模块是 HIGH 触发或可跳线切换
# True  → GPIO 输出 0 时继电器 ON (常见)
# False → GPIO 输出 1 时继电器 ON (少见, 或跳线切换)
RELAY_ACTIVE_LOW = os.getenv("AGENT_RELAY_LOW", "1") == "1"
