#!/usr/bin/env python3
"""XiaoLan Travel Calendar Bot — plans trips via chat and generates subscribable .ics calendars."""

import argparse
import asyncio
import logging
import threading
from pathlib import Path

import uvicorn
import yaml

import db
from models import Config
from bot import build_bot_app
from server import app as fastapi_app, set_data_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "travel_calendar.log"),
    ],
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> Config:
    config_path = Path(__file__).parent / path
    with open(config_path) as f:
        data = yaml.safe_load(f)
    return Config.from_yaml(data)


def run_fastapi(config: Config):
    """Run FastAPI server in a separate thread."""
    set_data_dir(config.data_dir)
    uvicorn.run(
        fastapi_app,
        host=config.server_host,
        port=config.server_port,
        log_level="info",
    )


async def main():
    parser = argparse.ArgumentParser(description="XiaoLan Travel Calendar Bot")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    config = load_config(args.config)
    db.init_db()

    logger.info("Starting XiaoLan Travel Calendar Bot")
    logger.info(f"Calendar server: {config.server_base_url}")
    logger.info(f"Claude model: {config.anthropic_model}")

    # Start FastAPI in a background thread
    api_thread = threading.Thread(target=run_fastapi, args=(config,), daemon=True)
    api_thread.start()
    logger.info(f"FastAPI server starting on {config.server_host}:{config.server_port}")

    # Start Telegram bot
    bot_app = build_bot_app(config)
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started")

    try:
        # Keep running
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
