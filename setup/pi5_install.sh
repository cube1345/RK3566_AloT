#!/usr/bin/env bash
# ===========================================================
# Agent ZX — 树莓派 5B (8GB) 一键部署脚本
# 适用: Raspberry Pi OS Bookworm (Debian 12, 64-bit)
# 用法: chmod +x pi5_install.sh && sudo ./pi5_install.sh
# ===========================================================
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python3"

echo "========================================"
echo " Agent ZX — Pi 5 一键部署"
echo "========================================"

# ── 1. 系统更新 + 硬件依赖 ──────────────────────────────
echo "[1/8] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq \
    git cmake build-essential \
    libgpiod-dev python3-gpiod \
    libopenblas-dev \
    python3-pip python3-venv \
    i2c-tools  # i2cdetect 调试

# ── 2. 启用硬件接口 ──────────────────────────────────────
echo "[2/8] 配置硬件接口..."
# 启用 UART (MH-Z19B)
if ! grep -q "^enable_uart=1" /boot/firmware/config.txt 2>/dev/null; then
    echo "enable_uart=1" >> /boot/firmware/config.txt
    echo "  ✓ UART 已启用 (需重启生效)"
fi
# 启用 I2C (SHT30, BH1750)
if ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" >> /boot/firmware/config.txt
    echo "  ✓ I2C 已启用 (需重启生效)"
fi

# ── 3. 创建 Python 虚拟环境 ──────────────────────────────
echo "[3/8] 创建 Python 虚拟环境..."
if [ ! -d "$REPO_DIR/.venv" ]; then
    $PYTHON -m venv "$REPO_DIR/.venv"
fi
source "$REPO_DIR/.venv/bin/activate"
pip install --upgrade pip -q

# ── 4. 安装 Python 依赖 ────────────────────────────────
echo "[4/8] 安装 Python 依赖..."
# 注释掉 requirements.txt 中的 gpiod, 改用系统包 python3-gpiod
pip install flask pyserial smbus2 pytest -q
echo "  ✓ pyserial (MH-Z19B UART)"
echo "  ✓ smbus2 (SHT30/BH1750 I2C)"
echo "  ✓ flask (Web Dashboard)"

# ── 5. 编译 llama.cpp ────────────────────────────────────
echo "[5/8] 编译 llama.cpp (OpenBLAS 加速)..."
LLAMA_DIR="$REPO_DIR/llama.cpp"
if [ ! -d "$LLAMA_DIR" ]; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
fi
cd "$LLAMA_DIR"
# Pi 5 Cortex-A76 支持 armv8.2-a+dotprod 指令集
cmake -B build \
    -DGGML_OPENBLAS=ON \
    -DCMAKE_C_FLAGS="-march=armv8.2-a+dotprod" \
    -DCMAKE_CXX_FLAGS="-march=armv8.2-a+dotprod"
cmake --build build --config Release -j"$(nproc)"
# 安装到 PATH
install -m 755 build/bin/llama-cli /usr/local/bin/llama-cli
install -m 755 build/bin/llama-server /usr/local/bin/llama-server
cd "$REPO_DIR"
echo "  ✓ llama-cli / llama-server 已安装"

# ── 6. 下载 Qwen2.5-1.5B GGUF ───────────────────────────
echo "[6/8] 下载 LLM 模型..."
MODEL_DIR="$REPO_DIR/models"
mkdir -p "$MODEL_DIR"
MODEL_PATH="$MODEL_DIR/Qwen2.5-1.5B-Instruct.Q4_K_M.gguf"
if [ ! -f "$MODEL_PATH" ]; then
    # HuggingFace mirror (hf-mirror.com 国内加速)
    pip install -q huggingface-hub
    $PYTHON -m huggingface_hub download \
        --resume-download \
        Qwen/Qwen2.5-1.5B-Instruct-GGUF \
        qwen2.5-1.5b-instruct-q4_k_m.gguf \
        --local-dir "$MODEL_DIR" \
        --local-dir-use-symlinks False
    echo "  ✓ 模型已下载"
else
    echo "  ✓ 模型已存在, 跳过"
fi

# ── 7. 配置环境变量 + 自启服务 ──────────────────────────
echo "[7/8] 配置服务..."
# 环境变量文件
cat > "$REPO_DIR/.env" << 'ENVEOF'
AGENT_MOCK=0
AGENT_MOCK_WEATHER=1
AGENT_GPIO=1
AGENT_PLATFORM=pi5
AGENT_UART=/dev/ttyAMA0
ENVEOF

# systemd 服务
cat > /etc/systemd/system/agent-zx.service << 'SVCEOF'
[Unit]
Description=Agent ZX Smart Home
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Agent_ZX
EnvironmentFile=/home/pi/Agent_ZX/.env
ExecStart=/home/pi/Agent_ZX/.venv/bin/python3 main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
systemctl daemon-reload
systemctl enable agent-zx

echo "  ✓ systemd 服务已注册: agent-zx"
echo "  ✓ 启动: sudo systemctl start agent-zx"
echo "  ✓ 状态: sudo systemctl status agent-zx"

# ── 8. 验证 + 总结 ──────────────────────────────────────
echo "[8/8] 验证安装..."
echo ""
# I2C
if i2cdetect -y 1 2>/dev/null | grep -q "44\|23"; then
    echo "  ✓ I2C 检测到传感器"
else
    echo "  ⚠ I2C 未检测到传感器 (接线后重试)"
fi
# 模型文件
if [ -f "$MODEL_PATH" ]; then
    MODEL_SIZE=$(du -h "$MODEL_PATH" | cut -f1)
    echo "  ✓ 模型文件: $MODEL_SIZE"
fi
# Python 环境
if "$REPO_DIR/.venv/bin/python3" -c "import serial; import smbus2" 2>/dev/null; then
    echo "  ✓ Python 依赖就绪"
fi

echo ""
echo "========================================"
echo " ✅ 部署完成!"
echo "========================================"
echo ""
echo "  需要重启以启用 UART/I2C:"
echo "    sudo reboot"
echo ""
echo "  重启后:"
echo "    sudo systemctl start agent-zx    # 启动服务"
echo "    journalctl -u agent-zx -f        # 查看日志"
echo "    http://<pi-ip>:5000              # 打开 Dashboard"
echo ""
echo "  切换平台 (默认 pi5):"
echo "    export AGENT_PLATFORM=rk3566"
echo ""
echo "========================================"
