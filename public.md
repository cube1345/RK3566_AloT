# Agent ZX 公共知识库

> 最后更新: 2026-05-01 | 版本: v5.0 (AI决策引擎升级)

## 项目总览

**Agent ZX** — 智能家居端侧 Agent 自动化管家，参加 2026 中兴捧月初赛·兴享智家赛道。

- **团队**: 1 人 (cube + Claude 副驾)
- **硬件目标**: Raspberry Pi 5 8GB (BCM2712, 4×A76@2.4GHz)
- **代码量**: ~3000 行 Python, 33 模块 (新增 ai_brain.py)
- **测试**: 98 passed / 103 total (核心30+压力46+传感器14+AI新增14), 5个预存smbus2失败(Windows)
- **LLM**: Qwen2.5-1.5B-Instruct Q4_K_M GGUF (941MB, 推理 ~10 tok/s)
- **当前分支**: master

## 架构核心 (v5.0)

```
传感器 → AIBrain(LLM主动决策30s周期) → 工具执行 → MidPath(自然语言说明) → Web Dashboard
       → FastPath(仅紧急安全网: temp>35/CO₂>2000)
       → 异常检测(z-score跳变跳过)
       → 偏好学习(用户覆盖→积累偏好)
```

### 决策架构 (v5.0 升级)
- **AIBrain**: LLM驱动的核心决策引擎, 每30s评估传感器快照, 决定是否行动
- **FastPath**: 降级为纯安全底线 (temp>35紧急降温, CO₂>2000紧急告警, light保持)
- **MidPath**: AI决策后异步生成自然语言说明
- **CommandHandler ReAct**: 复杂指令 执行→观察→再决策, 最多2轮迭代
- **偏好学习**: 用户覆盖AI决策后60s内记录, 高置信度偏好注入LLM上下文
- **异常检测**: z-score检测传感器跳变, 异常时跳过自动操作
- **主动洞察**: 每日08:00 LLM分析24h趋势, 生成个性化建议

### 7 大功能
1. ① 外卖/访客按键提醒 (GPIO按键 → TTS播报 + Dashboard)
2. ② 温湿度+空调/风扇自动调控 (AI决策为主 + FastPath紧急兜底)
3. ③ CO₂+空气自动净化 (AI决策为主 + FastPath紧急兜底)
4. ④ 亮度+灯光自动管理 (FastPath硬实时)
5. ⑤ 冰箱食材管理与保质期提醒 (NLU解析 → SQLite → 定时提醒)
6. ⑥ 外界温度+衣物推荐 (天气数据 → LLM穿衣建议)
7. ⑦ Web Dashboard 总控 (Flask + Chart.js, 12 API)

### 模块结构
- `core/`: agent.py (主循环), ai_brain.py (AI决策引擎, NEW), command_handler.py (ReAct循环), fastpath.py (安全网), midpath.py (AI自然语言补充), tool_registry.py (工具注册+并行执行)
- `agents/`: environment_agent.py, food_agent.py, life_agent.py (分层System Prompt + TOOLS定义)
- `sensors/`: co2.py, temp_humid.py, light.py, motion.py, mock.py
- `devices/`: fan.py, light.py, ac.py, purifier.py, manager.py, base.py
- `llm/`: engine.py, llamacpp_backend.py, rknn_backend.py, context.py
- `knowledge/`: database.py (6表: sensor_log, event_log, foods, home_tips, user_prefs, ai_decisions)
- `web/`: app.py (12 API), templates/, static/
- `tests/`: test_core.py (103 tests)

## 当前状态 (v5.0)

### 已完成
- [x] 项目骨架 + 配置系统
- [x] ToolRegistry + 并行执行引擎
- [x] FastPath 安全网 (仅紧急阈值)
- [x] Mock 传感器 + 4 真实传感器驱动
- [x] 4 设备驱动 (风扇/灯/净化器/空调)
- [x] LLM 推理 (llama.cpp CPU, temperature=0)
- [x] AI 决策引擎 (AIBrain: evaluate/detect_anomaly/generate_insight/learn_preference)
- [x] AI 主动决策循环 (_ai_poll_cycle 30s周期)
- [x] ReAct 多步推理 (CommandHandler._react_loop)
- [x] 异常检测 (z-score, 跳变跳过FastPath)
- [x] 偏好学习 (user_prefs表 + 60s覆盖检测)
- [x] AI 日洞察 (每日08:00趋势分析+建议)
- [x] MidPath AI决策补充 (异步自然语言说明)
- [x] Web Dashboard (12 API + Chart.js + 布局加固)
- [x] 98 测试全通 (14 新增AI测试)

### 待完成
- [ ] 真实传感器接线 + 端到端演示验证 (需Pi 5)
- [ ] LLM 模型下载到 Pi 5
- [ ] PDF方案文档排版
- [ ] 演示视频录制 (≤3min)

## 合作方式

- cube 指方向，Claude 负责落地
- 决策优先序: 正确 > 质量 > 速度 > 简洁
- 先读后答，工具验证优先
- 自主推进，遇错自修；架构决策进入Plan模式
- 原子 commit，写完就提交
