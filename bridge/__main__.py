"""Run with ``python -m bridge``."""

import asyncio
import logging
import os

from .config import BridgeConfig
from .service import PictoChatBridge


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("PICTOCHAT_LOG_LEVEL", "INFO").upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(PictoChatBridge(BridgeConfig.from_env()).run_forever())


if __name__ == "__main__":
    main()
