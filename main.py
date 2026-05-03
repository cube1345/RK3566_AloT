# Agent_ZX - 智能家居端侧 Agent 自动化管家
# 赛题入口: main.run()

import asyncio
from core.agent import AgentOrchestrator


async def main():
    orchestrator = AgentOrchestrator()
    try:
        await orchestrator.start()
        await orchestrator.run()
    except KeyboardInterrupt:
        pass
    finally:
        await orchestrator.stop()


def run():
    """竞赛入口函数 — 启动 Agent ZX 端侧AI智能家居管家"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
