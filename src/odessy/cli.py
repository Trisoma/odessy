from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from odessy.agent_loop import OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy


async def _main_async(user_text: str) -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=Path.cwd()),
        mcp_server=build_odessy_mcp_server(workspace_root=Path.cwd()),
    )
    result = await agent.run_turn(user_text)
    print(result.final_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Odessy fake-model loop.")
    parser.add_argument("text", help="User text to send into the Odessy loop.")
    args = parser.parse_args()
    asyncio.run(_main_async(args.text))


if __name__ == "__main__":
    main()
