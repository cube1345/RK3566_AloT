#!/bin/bash
# Agent ZX 一键开发环境配置
set -e

echo "=== Agent ZX 环境配置 ==="

# 创建必要目录
mkdir -p data models

# 安装 Python 依赖
pip install flask pyserial smbus2 pytest pytest-asyncio -q

# Mock 模式默认开启 (不需要硬件)
echo "AGENT_MOCK=1" > .env
echo "AGENT_MOCK_WEATHER=1" >> .env

echo "=== 完成 ==="
echo "运行: python3 main.py"
echo "Mock 模式: 无需硬件, 传感器数据模拟生成"
echo ""
echo "部署到 RK3588 前:"
echo "  1. 下载模型到 models/"
echo "  2. 设置 AGENT_MOCK=0 AGENT_GPIO=1"
