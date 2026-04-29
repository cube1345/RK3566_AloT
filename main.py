# Agent_ZX - 智能家居端侧 Agent 自动化管家
# 入口文件

import asyncio
import signal
import sys
from core.agent import AgentOrchestrator


async def main():
    orchestrator = AgentOrchestrator()
    try:
        await orchestrator.start()
        # 主循环: 传感器轮询 + 事件监听 + LLM 按需推理
        await orchestrator.run()
    except KeyboardInterrupt:
        pass
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
