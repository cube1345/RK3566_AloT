# Agent ZX 自动化测试与优化任务

## 工作协议
1. **停手保证**：完成当前条件所有可达目标后立即停手，不新增未要求的特性，不重构未要求的代码
2. **记录保证**：每个逻辑改动后必须 git add + git commit，commit message 写明改了啥和为什么
3. **不越界**：不修改 prompt_auto.md 本身，不改动 .gitignore/setup.sh 以外的项目配置

## 安全约束
1. 不得修改 ~/.bashrc、~/.profile、系统级配置文件
2. 下载资源只限本项目文件夹 /home/cube/WorkSpace/DLML/Agent_ZX/
3. 不安装全局软件包，只在项目 .venv 内操作
4. 每完成一个逻辑改动就 git add + git commit（原子提交）
5. 不改动 .gitignore、setup.sh 以外的项目配置

## 项目背景
/home/cube/WorkSpace/DLML/Agent_ZX 是一个智能家居 Agent 系统，参赛 2026 中兴捧月。
硬件目标 RK3588，当前在 Linux x86_64 开发。
代码 ~2000 行 / 28 模块，uv 虚拟环境在 .venv/。
Qwen2.5-1.5B-Instruct Q4_K_M GGUF 模型已下载到 models/。

## 启动流程
source .venv/bin/activate
export PYTHONPATH=

## 核心模块
- core/tool_registry.py: 工具注册与并行执行
- core/fastpath.py: 规则引擎
- core/midpath.py: 异步 LLM 补充
- core/command_handler.py: 用户指令→Agent路由→LLM→工具执行
- core/agent.py: AgentOrchestrator 主循环
- llm/llamacpp_backend.py: llama.cpp 推理
- llm/engine.py: 双后端 LLM 引擎
- sensors/mock.py: Mock 传感器
- devices/: 4 个设备驱动
- knowledge/database.py: SQLite
- agents/: 3 个分层 Agent prompt
- web/app.py: Flask Dashboard
- tests/test_core.py: 现有 8 个测试

## 已知问题（需要修复）
1. 食材 Agent LLM 输出不稳定：指令"买了鸡蛋明天到期"路由到 food agent，但 Qwen1.5B 倾向输出"已记录：鸡蛋..."的对话回复而非 JSON 工具调用，导致 add_food 未执行
2. 生活 Agent 同样问题：指令"今天穿什么"路由到 life agent，但 LLM 直接输出穿衣建议文字，未调用 get_weather 工具
3. 环境 Agent 已验证工作："太热了"→ac_control+set_fan ✅

## 优化任务（按优先级）

### P0：修复 LLM 结构化输出
目标：让所有 Agent 输出正确的 JSON 工具链而非对话文本
可尝试的方案：
a) 修改 core/command_handler.py 中 system_prompt 构建方式，添加更强约束
b) 在 agents/*.py 的 SYSTEM_PROMPT 末尾追加 JSON 输出指令
c) 设置 LLM temperature=0（在 config.py LLM_TEMPERATURE 或调用时）
d) 对食材类指令增加第二层兜底：正则提取（关键词"到期""过期"匹配日期）
e) 最终标准：发送 "买了鸡蛋明天到期" → actions 包含 add_food 调用

### P1：补充测试
- 测试 CommandHandler.handle() 返回格式正确
- 测试 Agent 路由关键词覆盖
- 测试 _parse_tool_chain 兼容 3 种格式（完整 JSON / 扁平列表 / 代码块）
- 测试 execute_plan 空列表/单工具/多工具
- 测试 LLM.generate_structured 输出可解析

### P2：补全缺失工具
- agents/life_agent.py 中 TOOLS 引用了 generate_daily_report 但 register 里没有
- 可在 core/agent.py _register_tools() 中添加

### P3：系统稳定性
- 验证 main.py 能启动并稳定运行 30s 不崩溃
- 验证 Web API 全部返回 200
- 验证传感器轮询入库正常

## 验证方法

每个改动后运行：
source .venv/bin/activate && PYTHONPATH= python -m pytest tests/ -v
然后手动测试关键链路（见上文"最终标准"）

## 退出条件

**停手条件（满足任一即停）：**
1. 所有 P0 任务完成 → 所有 Agent 路由能正确触发工具调用
2. 所有已有测试通过（现有 8 个 + 新加的）
3. main.py 能启动稳定运行 30s 不崩溃
4. 以上条件无法达成时报错退出（不陷入死循环）

**停手后：**
- 记录最终状态到可行性方案.md（追加"自动化优化记录"章节）
- 执行 git log --oneline -10 展示本次所有提交
- 输出"所有可达目标已完成，停手"并退出
