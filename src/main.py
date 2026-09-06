import asyncio
import logging
import signal
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.config import settings
from src.handlers import router
from src.uptime import UptimeMonitor, UptimeStore


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stdout,
    )
    store = UptimeStore(settings.UPTIME_DB, settings.UPTIME_TIMEZONE)
    monitor = UptimeMonitor(
        store, settings.ADMIN_IDS[0] if settings.ADMIN_IDS else None
    )
    dp = Dispatcher(uptime_store=store)
    dp.include_router(router)

    # Handle shutdown even while booting without an internet connection.
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    logging.info("Bot initialized; waiting for Telegram connectivity.")

    async with Bot(
        token=settings.TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:

        async def poll_when_online() -> None:
            await monitor.ready.wait()
            await dp.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                tasks_concurrency_limit=16,
            )
            if not stopped.is_set():
                raise RuntimeError("Polling stopped unexpectedly")

        try:
            async with asyncio.TaskGroup() as group:
                monitoring = group.create_task(monitor.run(bot))
                polling = group.create_task(poll_when_online())
                await stopped.wait()
                try:
                    await dp.stop_polling()
                except RuntimeError:
                    # Polling has not started yet when the host boots offline.
                    pass
                monitoring.cancel()
                polling.cancel()
            if monitor.started:
                store.stopped(time.time())
            logging.info("Bot stopped.")
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


if __name__ == "__main__":
    asyncio.run(main())
