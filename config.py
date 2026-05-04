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
    "k210": 0.5,      # K210 摄像头轮询间隔(秒), 高频以实时捕获事件
}

# ===== 传感器型号选择 (MOCK_SENSORS=0 时生效) =====
# CO₂: "sgp30" (I2C) / "mhz19b" (UART)
SENSOR_CO2 = os.getenv("AGENT_SENSOR_CO2", "sgp30").lower()
# 温湿度: "dht11" (GPIO) / "sht30" (I2C)
SENSOR_TEMP = os.getenv("AGENT_SENSOR_TEMP", "dht11").lower()

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
    "sgp30": 0x58,   # SGP30 固定地址
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

# ===== UART (K210 摄像头) =====
# Pi 5:  /dev/ttyAMA1 (独立于 CO₂ 的 /dev/ttyAMA0)
# RK3566: /dev/ttyS3
if PLATFORM == "pi5":
    _DEFAULT_K210_UART = "/dev/ttyAMA1"
elif PLATFORM == "rk3566":
    _DEFAULT_K210_UART = "/dev/ttyS3"
else:
    _DEFAULT_K210_UART = "/dev/ttyS1"
K210_UART = os.getenv("AGENT_K210_UART", _DEFAULT_K210_UART)
K210_BAUD = int(os.getenv("AGENT_K210_BAUD", "115200"))

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

# ===== AI Decision Engine =====
AI_EVAL_INTERVAL = 30          # AI决策评估间隔(秒)
AI_MAX_REACT_ITER = 2          # ReAct最大迭代轮次
AI_ANOMALY_THRESHOLD = 3.0     # z-score异常检测阈值
AI_MIN_CONFIDENCE = 0.3        # 偏好最低置信度(低于此值不注入LLM)

# ===== 场景识别 =====
SCENE_TRIGGERS = {
    "sleep": {
        "name": "睡觉",
        "keywords": ["晚安", "睡了", "睡觉", "困了", "休息"],
        "auto_hour_range": (22, 7),  # 22:00-07:00自动触发
        "tools": [
            {"tool": "set_light", "params": {"state": "off"}},
            {"tool": "ac_control", "params": {"mode": "cool", "temp": 26, "fan_speed": "low"}},
            {"tool": "set_fan", "params": {"speed": 0}},
        ],
        "reply": "晚安！已关灯、空调26°C、免打扰模式。好梦～",
    },
    "away": {
        "name": "出门",
        "keywords": ["出门", "走了", "拜拜", "再见", "离开"],
        "tools": [
            {"tool": "set_light", "params": {"state": "off"}},
            {"tool": "ac_control", "params": {"mode": "off"}},
            {"tool": "set_fan", "params": {"speed": 0}},
            {"tool": "set_air_purifier", "params": {"level": 0}},
        ],
        "reply": "已关闭全部设备。路上注意安全！",
    },
    "home": {
        "name": "回家",
        "keywords": ["回来了", "到家", "我回来了", "到家了"],
        "tools": [
            {"tool": "ac_control", "params": {"mode": "cool", "temp": 26, "fan_speed": "auto"}},
        ],
        "reply": "欢迎回家！空调已开启。",
    },
    "movie": {
        "name": "观影",
        "keywords": ["看电影", "看剧", "追剧", "观影"],
        "tools": [
            {"tool": "set_light", "params": {"state": "on", "brightness": 50}},
            {"tool": "ac_control", "params": {"mode": "cool", "temp": 25, "fan_speed": "low"}},
        ],
        "reply": "观影模式：灯光调暗、空调25°C、免打扰。",
    },
    "wakeup": {
        "name": "起床",
        "keywords": ["起床", "早安", "早上好", "醒了"],
        "tools": [
            {"tool": "set_light", "params": {"state": "on", "brightness": 200}},
            {"tool": "ac_control", "params": {"mode": "off"}},
        ],
        "reply": "早上好！新的一天开始了。",
    },
    "cooking": {
        "name": "烹饪",
        "keywords": ["做饭", "炒菜", "煮饭", "烹饪", "烧菜"],
        "tools": [
            {"tool": "set_air_purifier", "params": {"level": 2}},
        ],
        "reply": "开始做饭了，已开通风净化。",
    },
}

# ===== 继电器触发极性 =====
# 大多数国产模块是 LOW 触发 (IN=LOW → 继电器吸合)
# 少数模块是 HIGH 触发或可跳线切换
# True  → GPIO 输出 0 时继电器 ON (常见)
# False → GPIO 输出 1 时继电器 ON (少见, 或跳线切换)
RELAY_ACTIVE_LOW = os.getenv("AGENT_RELAY_LOW", "1") == "1"
