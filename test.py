import asyncio
from logs import AsyncLogger, LogLevel


async def async_main():
    logger = AsyncLogger.get_logger(level=LogLevel.INFO)

    try:
        import json
        from datetime import datetime
        json.dumps(dict(datetime=datetime.now()))
    except Exception:
        await logger.info("OOPS", exc_info=True)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(async_main())
