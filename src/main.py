from asyncio import run
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from src.handlers.commands_handler import router as main_router
from src.handlers.errors_handler import router as errors_router
from src.internal.config import Settings, setup_logs
from src.internal.notify_admin import on_shutdown, on_startup
from src.middlewares.logging_middleware import LoggingMiddleware
from src.middlewares.updates_dumper_middleware import UpdatesDumperMiddleware


async def main():
    setup_logs('torrent_bot')
    settings = Settings()

    bot = Bot(
        token=settings.TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage, settings=settings)

    dispatcher.update.outer_middleware(UpdatesDumperMiddleware())
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)
    dispatcher.message.middleware.register(LoggingMiddleware())
    dispatcher.callback_query.middleware.register(LoggingMiddleware())
    dispatcher.include_routers(
        main_router,
        errors_router,
    )

    await dispatcher.start_polling(bot)
    logging.info("Torrent bot started")


def run_main():
    run(main())


if __name__ == '__main__':
    run_main()
