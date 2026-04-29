#!/bin/bash
# Agent ZX 一键开发环境配置 (uv)
set -e

echo "=== Agent ZX 环境配置 ==="
mkdir -p data models

# 创建 uv 虚拟环境 + 安装依赖
uv venv --python 3.11 2>/dev/null || true
source .venv/bin/activate
uv pip install flask pyserial smbus2 pytest pytest-asyncio requests -q

# Mock 模式默认开启 (无需硬件)
echo "AGENT_MOCK=1" > .env
echo "AGENT_MOCK_WEATHER=1" >> .env

echo ""
echo "=== 完成 ==="
echo "启动: source .venv/bin/activate && python main.py"
echo "测试: source .venv/bin/activate && python -m pytest tests/ -v"
echo "Web:   http://localhost:5000"
echo ""
echo "部署到 RK3588 前:"
echo "  1. 下载模型到 models/"
echo "  2. 设置 AGENT_MOCK=0 AGENT_GPIO=1"
