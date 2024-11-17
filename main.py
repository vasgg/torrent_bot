from asyncio import run
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import Settings, setup_logs
from lexicon import texts

settings = Settings()


async def start(update: Update, context):
    await update.message.reply_text(texts['welcome'].format(username=update.message.from_user.full_name))


async def handle_file(update: Update, context):
    user_id = update.message.from_user.id
    if user_id not in settings.ADMINS:
        return

    file = update.message.document
    if not file.file_name.endswith(".torrent"):
        await update.message.reply_text("Принимаются только .torrent файлы.")
        return

    target_folder = Path(settings.FOLDER)
    file_path = target_folder / file.file_name
    try:
        file_data = await file.get_file()
        await file_data.download_to_drive(str(file_path))
        await update.message.reply_text(f"Файл сохранён: {file.file_name}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении файла: {e}")
        await update.message.reply_text(f"Не удалось сохранить файл: {file.file_name}")


async def notify_admin(bot, message):
    try:
        await bot.send_message(chat_id=settings.ADMINS[0], text=message)
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение админу {settings.ADMINS[0]}: {e}")


async def on_startup(app: Application):
    await notify_admin(app.bot, "Бот запущен и готов к работе.")
    logging.info("Bot started")


async def on_shutdown(app: Application):
    await notify_admin(app.bot, "Бот завершает работу.")
    logging.info("Bot stopped")


def main():
    setup_logs('torrent_bot')
    app = Application.builder().token(settings.TOKEN.get_secret_value()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    app.post_init_callback = lambda: run(on_startup(app))
    app.shutdown_callback = lambda: run(on_shutdown(app))

    app.run_polling()


if __name__ == '__main__':
    main()
