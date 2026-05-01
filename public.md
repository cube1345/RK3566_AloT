# Agent ZX 公共知识库

> 最后更新: 2026-05-01 | 版本: v5.1 (场景+反问+知识驱动)

## 项目总览

**Agent ZX** — 智能家居端侧 Agent 自动化管家，参加 2026 中兴捧月初赛·兴享智家赛道。

- **团队**: 1 人 (cube + Claude 副驾)
- **硬件目标**: Raspberry Pi 5 8GB (BCM2712, 4×A76@2.4GHz)
- **代码量**: ~3500 行 Python, 35 模块 (新增 ai_brain.py, scene_engine.py)
- **测试**: 117 passed / 123 total, 5个预存smbus2失败(Windows), 1个async跳过
- **LLM**: Qwen2.5-1.5B-Instruct Q4_K_M GGUF (941MB, 推理 ~10 tok/s)
- **当前分支**: master

## 架构核心 (v5.1)

```
用户指令 → SceneEngine(6场景关键词) → 工具执行 → 自然语言回复
                                        ↓ 未命中
          → CommandHandler(ReAct/L路由) → 工具执行 → MidPath说明 → Web Dashboard
传感器 → AIBrain(LLM决策+知识上下文+反问) → 工具执行
       → FastPath(仅紧急安全网: temp>35/CO₂>2000)
       → 异常检测(z-score跳变跳过) → AI反问用户
       → 偏好学习(用户覆盖→积累偏好,注入LLM)
```

### 决策架构 (v5.1 升级)
- **SceneEngine** (NEW): 6预定义场景(sleep/away/home/movie/wakeup/cooking), 关键词触发+时间自动触发
- **AIBrain知识上下文**: 决策前查询历史同时段对比(same_hour)+日均趋势(hourly_profile)+传感器关联(correlation)
- **AIBrain主动反问**: 不确定时返回question类型 → Dashboard 5s轮询 → 用户回答 → 执行/忽略
- **AIBrain**: LLM决策引擎, 每30s评估, 支持3种返回类型(action/question/none)
- **FastPath**: 仅紧急安全底线 (temp>35紧急降温, CO₂>2000紧急告警)
- **MidPath**: AI决策后异步生成自然语言说明
- **CommandHandler ReAct**: 复杂指令 执行→观察→再决策, 最多2轮
- **偏好学习+异常检测**: 用户覆盖记录, z-score跳变跳过

### 7 大功能
1. ① 外卖/访客按键提醒 (GPIO按键 → TTS播报 + Dashboard)
2. ② 温湿度+空调/风扇自动调控 (AI决策为主 + FastPath紧急兜底)
3. ③ CO₂+空气自动净化 (AI决策为主 + FastPath紧急兜底)
4. ④ 亮度+灯光自动管理 (FastPath硬实时)
5. ⑤ 冰箱食材管理与保质期提醒 (NLU解析 → SQLite → 定时提醒)
6. ⑥ 外界温度+衣物推荐 (天气数据 → LLM穿衣建议)
7. ⑦ Web Dashboard 总控 (Flask + Chart.js, 12 API)

### 模块结构
- `core/`: agent.py (主循环), ai_brain.py (AI决策+知识+反问), scene_engine.py (6场景识别, NEW), command_handler.py (ReAct循环+场景入口), fastpath.py (安全网), midpath.py (AI自然语言), tool_registry.py (工具注册+并行执行)
- `agents/`: environment_agent.py, food_agent.py, life_agent.py (分层System Prompt + TOOLS)
- `sensors/`: co2.py, temp_humid.py, light.py, motion.py, mock.py
- `devices/`: fan.py, light.py, ac.py, purifier.py, manager.py, base.py
- `llm/`: engine.py, llamacpp_backend.py, rknn_backend.py, context.py
- `knowledge/`: database.py (8表: sensor_log, event_log, foods, home_tips, user_prefs, ai_decisions, routines + 3知识查询方法)
- `web/`: app.py (14 API), templates/dashboard.html (提问UI)
- `tests/`: test_core.py (123 tests)

## 当前状态 (v5.1)

### 已完成
- [x] 项目骨架 + 配置系统
- [x] ToolRegistry + 并行执行引擎
- [x] FastPath 安全网 (仅紧急阈值)
- [x] Mock 传感器 + 4 真实传感器驱动
- [x] 4 设备驱动 (风扇/灯/净化器/空调)
- [x] LLM 推理 (llama.cpp CPU, temperature=0)
- [x] v5.0: AI 决策引擎 (evaluate/detect_anomaly/generate_insight/learn_preference)
- [x] v5.0: AI 主动决策循环 + ReAct 多步推理 + 偏好学习 + 日洞察
- [x] v5.1: 场景识别引擎 (6场景, 关键词+时间自动触发)
- [x] v5.1: AI主动反问 (question输出类型 + Dashboard 5s轮询)
- [x] v5.1: 知识驱动决策 (同时段对比+日均趋势+传感器关联+routines规律)
- [x] v5.1: Web Dashboard (14 API, 提问UI)
- [x] 117 测试全通 (19 v5.1新增)

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
