"""
闲鱼 Bot systemd 服务入口
运行: python bot_service.py
"""

import asyncio, logging, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVICE] %(message)s")
log = logging.getLogger("bot_service")

async def main():
    log.info("Starting Xianyu Bot Service...")
    try:
        from bot_monitor import XianyuBot
        bot = XianyuBot()
        await bot.start()
    except ImportError as e:
        log.error(f"Import error: {e}")
        log.error("Make sure playwright is installed: pip install playwright && python -m playwright install chromium")
        sys.exit(1)
    except KeyboardInterrupt:
        log.info("Service stopped")
    except Exception as e:
        log.error(f"Fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
