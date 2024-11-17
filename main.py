import logging
import os

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
        await update.message.reply_text("Я принимаю только .torrent файлы.")
        return

    file_path = os.path.join(settings.FOLDER, file.file_name)
    file_data = await file.get_file()
    await file_data.download_to_drive(file_path)

    await update.message.reply_text(f"Файл сохранён: {file.file_name}")


def main():
    setup_logs('torrent_bot')
    app = Application.builder().token(settings.TOKEN.get_secret_value()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    logging.info("Bot started")
    app.run_polling()


if __name__ == '__main__':
    main()
