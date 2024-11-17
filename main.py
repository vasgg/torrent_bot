from asyncio import run
import logging
from pathlib import Path
from torrent_parser import TorrentFileParser

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import Settings, setup_logs
from lexicon import texts

settings = Settings()


async def start(update: Update, context):
    uptime = get_uptime_message()
    message = (
        texts['welcome'].format(username=update.message.from_user.full_name) +
        f"\n\n{uptime}"
    )
    await update.message.reply_text(message)


def get_uptime_message() -> str:
    try:
        with open('/proc/uptime') as f:
            uptime_seconds = float(f.readline().split()[0])

        days = int(uptime_seconds // (24 * 3600))
        hours = int((uptime_seconds % (24 * 3600)) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)

        return f"System uptime: {days} days, {hours} hours, {minutes} minutes."
    except Exception as e:
        logging.error(f"Unable to get system uptime: {e}")
        return "Unable to get system uptime."


def get_torrent_info(file_path):
    parser = TorrentFileParser(file_path)
    data = parser.parse()
    name = data['info'].get('name', 'Unknown')
    files = data['info'].get('files', [])
    total_size = sum(f['length'] for f in files) if files else data['info'].get('length', 0)
    formatted_files = "\n".join(
        [f"- {f['path'][0]} ({f['length'] / (1024 * 1024):.2f} MB)" for f in files]
    ) if files else f"- {name} ({total_size / (1024 * 1024):.2f} MB)"

    return f"File name: {name}\nTotal size: {total_size / (1024 * 1024):.2f} MB\nFiles:\n{formatted_files}"


async def handle_file(update: Update, context):
    user_id = update.message.from_user.id
    if user_id not in settings.ADMINS:
        return

    file = update.message.document
    if not file.file_name.endswith(".torrent"):
        await update.message.reply_text("Accept only .torrent files.")
        return

    target_folder = Path(settings.FOLDER)
    file_path = target_folder / file.file_name

    existing_files = list(target_folder.glob(f"{file.file_name}*"))
    if existing_files:
        await update.message.reply_text(
            f"File with the same name already exists: {existing_files[0].name}"
        )
        return

    try:
        file_data = await file.get_file()
        await file_data.download_to_drive(str(file_path))

        with open(file_path, "rb") as torrent_file:
            torrent_info = get_torrent_info(torrent_file)

        await update.message.reply_text(
            f"File saved: {file.file_name}\n\nTorrent info:\n{torrent_info}"
        )
    except Exception as e:
        logging.error(f"Unable to save file: {e}")
        await update.message.reply_text(f"Unable to save file: {file.file_name}")


async def notify_admin(bot, message):
    try:
        await bot.send_message(chat_id=settings.ADMINS[0], text=message)
    except Exception as e:
        logging.error(f"Unable to send message to admin {settings.ADMINS[0]}: {e}")


async def on_startup(bot):
    await notify_admin(bot, "Bot started.")
    logging.info("Bot started.")


async def on_shutdown(bot):
    uptime = get_uptime_message()
    try:
        message = f"Bot stopped.\n\nUptime:\n{uptime}"
        await bot.send_message(chat_id=settings.ADMINS[0], text=message)
    except Exception as e:
        logging.error(f"Unable to send message to admin {settings.ADMINS[0]}: {e}")


def main():
    setup_logs('torrent_bot')
    app = Application.builder().token(settings.TOKEN.get_secret_value()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    app.post_init_callback = lambda: run(on_startup(app.bot))
    app.shutdown_callback = lambda: run(on_shutdown(app.bot))

    app.run_polling()


if __name__ == '__main__':
    main()
