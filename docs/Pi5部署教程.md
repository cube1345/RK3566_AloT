# Agent ZX 部署到 Raspberry Pi 5 教程 (MobaXterm)

> 适用环境: Windows 11 + MobaXterm → Raspberry Pi 5 (Raspberry Pi OS 64-bit)
> 预计耗时: 30-45 分钟

---

## 一、Pi 5 基础环境准备

### 1.1 烧录系统

1. 下载 [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. 选择: **Raspberry Pi 5** → **Raspberry Pi OS (64-bit)** (推荐 Lite 版, 无需桌面)
3. 烧录前点齿轮图标设置:
   - 开启 SSH
   - 设置用户名/密码
   - 配置 WiFi (如果不用网线)
4. 烧录到 SD 卡, 插入 Pi 5, 通电启动

### 1.2 通过 MobaXterm SSH 连接

1. 打开 MobaXterm → Session → SSH
2. Remote host: Pi 5 的 IP (路由器后台查看, 或 `ping raspberrypi.local`)
3. 用户名/密码: 上一步设置的
4. 连接成功后进入终端

### 1.3 系统基础配置

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装必要工具
sudo apt install -y git python3-pip python3-venv i2c-tools gpiod lm-sensors

# 启用 I2C 接口
sudo raspi-config nonint do_i2c 0

# (如果不用 DHT11 用 SHT30) 启用 I2C 即可, 无需额外配置

# 如果曾计划用 MH-Z19B 需启用 UART, 当前默认 SGP30 不需要:
# sudo raspi-config nonint do_serial 0

# 重启使配置生效
sudo reboot
```

### 1.4 验证 I2C 设备

```bash
# 查看 I2C 总线
ls /dev/i2c-*

# 扫描 I2C 设备 (接线后执行)
i2cdetect -y 1
# 应看到: 0x23 (BH1750), 0x58 (SGP30)
```

---

## 二、传输代码到 Pi 5

### 方法 A: MobaXterm 拖拽上传 (最简单)

1. 在 MobaXterm 左侧文件浏览器, 定位到 `/home/pi/`
2. 在 Windows 资源管理器打包项目为 ZIP (排除 `.venv`, `models/`, `data/`, `__pycache__`)
3. 直接拖拽 ZIP 到 MobaXterm 左侧面板
4. 回到终端解压:

```bash
cd ~
unzip Agent_ZX.zip -d Agent_ZX
cd Agent_ZX
```

### 方法 B: Git 克隆 (推荐, 可后续更新)

```bash
# 如果代码已推送到 GitHub/Gitee
cd ~
git clone <你的仓库地址> Agent_ZX
cd Agent_ZX
```

### 方法 C: SCP 手动传 (备选)

在 Windows 终端 (PowerShell):
```powershell
scp -r E:\WorkSpace\WSL_WorkSpace\Agent_ZX\Agent_ZX pi@<Pi5_IP>:/home/pi/Agent_ZX
```

---

## 三、安装 Python 依赖

```bash
cd ~/Agent_ZX

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install flask pyserial smbus2 gpiod pytest pytest-asyncio requests

# 如果 DHT11 不走 gpiod (如用 lgpio 替代), 额外装:
# pip install rpi-lgpio
```

---

## 四、下载 LLM 模型

```bash
cd ~/Agent_ZX
mkdir -p models

# 方案1: 从 huggingface 下载 (Pi 5 上直接下, 约 941MB, 需 10-30min)
pip install huggingface_hub
huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
  Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
  --local-dir models/ --local-dir-use-symlinks False

# 方案2: 在 Windows 上下好, 用 MobaXterm 拖到 ~/Agent_ZX/models/
# 文件: Qwen2.5-1.5B-Instruct-Q4_K_M.gguf (941MB)

# 确认模型文件存在
ls -lh models/
```

> **注意**: 如果 Pi 5 下载太慢, 在 Windows 上用迅雷/IDM 下载后通过 MobaXterm 拖拽上传更快。

---

## 五、安装 llama.cpp

```bash
cd ~

# 克隆并编译
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
make -j4  # Pi 5 四核编译, 约 5 分钟

# 安装 Python 绑定
cd ~/Agent_ZX
source .venv/bin/activate
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# 验证
python -c "from llama_cpp import Llama; print('OK')"
```

> 编译约需 5-10 分钟, 期间 CPU 满载属正常现象。确保散热片/风扇已装好。

---

## 六、配置环境变量

```bash
cd ~/Agent_ZX

# 创建 .env 文件
cat > .env << 'EOF'
# 关闭 Mock, 使用真实传感器
AGENT_MOCK=0
AGENT_MOCK_WEATHER=0

# 传感器: SGP30 + DHT11
AGENT_SENSOR_CO2=sgp30
AGENT_SENSOR_TEMP=dht11

# GPIO 启用
AGENT_GPIO=1

# LLM 模型路径
AGENT_LLM=models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf

# 平台
AGENT_PLATFORM=pi5
EOF

# 查看配置
cat .env
```

---

## 七、接线验证

在安装传感器之前, 先验证 GPIO 和 I2C 工作正常:

```bash
# I2C: 接好 SGP30 + BH1750 后扫描
i2cdetect -y 1

# GPIO: 测试继电器 (灯那路)
source .venv/bin/activate
python -c "
from devices.light import LightDevice
l = LightDevice()
l.control('on')
import time; time.sleep(2)
l.control('off')
"
# 应听到继电器 '咔嗒' 吸合+断开两声
```

---

## 八、运行测试

```bash
cd ~/Agent_ZX
source .venv/bin/activate

# 跑全量测试 (排除硬件相关)
python -m pytest tests/test_core.py -v \
  --ignore-glob='*sensor*' \
  -k 'not TestSensorDrivers' \
  --tb=short

# 应看到 110+ passed, 0 failed
```

---

## 九、启动系统

```bash
cd ~/Agent_ZX
source .venv/bin/activate

# 前台运行 (调试用, Ctrl+C 退出)
python main.py

# 后台运行 (长期)
nohup python main.py > agent.log 2>&1 &

# 查看日志
tail -f agent.log
```

启动后访问: **`http://<Pi5_IP>:5000`** 即可看到 Dashboard。

---

## 十、设置开机自启 (可选)

```bash
# 创建 systemd 服务
sudo tee /etc/systemd/system/agent-zx.service << 'EOF'
[Unit]
Description=Agent ZX Smart Home
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Agent_ZX
ExecStart=/home/pi/Agent_ZX/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 启用
sudo systemctl daemon-reload
sudo systemctl enable agent-zx
sudo systemctl start agent-zx

# 查看状态
sudo systemctl status agent-zx
```

---

## 十一、快速排错

| 现象 | 检查 |
|------|------|
| `i2cdetect -y 1` 无设备 | 接线松动, 检查 SDA/SCL/VCC/GND |
| SGP30 读到固定 400ppm | 预热不足, 等 15s 后正常 |
| DHT11 频繁报超时 | DHT11 对时序敏感, GPIO 位拆裂偶发失败属正常(驱动有缓存容错) |
| `ModuleNotFoundError: smbus2` | `pip install smbus2` |
| `ModuleNotFoundError: gpiod` | `pip install gpiod` |
| LLM 加载失败 | 检查模型路径, 确认文件大小 941MB |
| Dashboard 打不开 | 检查防火墙: `sudo ufw allow 5000` |
| 继电器不吸合 | 确认 RELAY_ACTIVE_LOW 与模块匹配(默认 LOW触发) |

---

## 附录: 完整部署命令汇总 (复制粘贴)

```bash
# === Pi 5 上一次性执行 ===

# 系统准备
sudo apt update && sudo apt install -y git python3-pip python3-venv i2c-tools gpiod
sudo raspi-config nonint do_i2c 0
sudo reboot

# (重启后重新 SSH 连接)
cd ~

# 获取代码 (选其中一种方式)
# git clone <仓库地址> Agent_ZX
# 或用 MobaXterm 拖拽上传后 unzip

cd ~/Agent_ZX
python3 -m venv .venv
source .venv/bin/activate
pip install flask pyserial smbus2 gpiod pytest pytest-asyncio requests

# 编译 llama.cpp
cd ~
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp && make -j4
cd ~/Agent_ZX
source .venv/bin/activate
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# 下载模型 (Pi 5 直下或用 MobaXterm 上传)
mkdir -p models
# ... 模型放 models/ 目录 ...

# 配置
cat > .env << 'EOF'
AGENT_MOCK=0
AGENT_SENSOR_CO2=sgp30
AGENT_SENSOR_TEMP=dht11
AGENT_GPIO=1
AGENT_LLM=models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
AGENT_PLATFORM=pi5
EOF

# 测试
python -m pytest tests/test_core.py -v -k 'not TestSensorDrivers' --tb=short

# 启动
python main.py
```
