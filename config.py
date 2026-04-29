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

# ===== GPIO Pin 定义 =====
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

# ===== I2C 地址 =====
I2C_ADDR = {
    "sht30": 0x44,
    "bh1750": 0x23,
}

# ===== UART =====
UART_DEV = "/dev/ttyS0"  # MH-Z19B
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
