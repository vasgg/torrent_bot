import logging
from pathlib import Path

from aiogram import Router, F, types, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.config import settings
from src.utils import get_uptime_message, get_torrent_info

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    uptime = get_uptime_message()
    text = (
        f"Greetings, {message.from_user.full_name} 👋\n\n"
        "Drop the torrent file to download it on the server.\n\n"
        f"{uptime}"
    )
    await message.answer(text)


@router.message(F.document, F.from_user.id.in_(settings.ADMIN_IDS))
async def handle_torrent_file(message: Message, bot: Bot):
    document = message.document
    if not document.file_name.endswith(".torrent"):
        await message.answer("Accept only .torrent files.")
        return

    target_folder = Path(settings.TORRENT_DIR)
    target_folder.mkdir(parents=True, exist_ok=True)
    
    file_path = target_folder / document.file_name

    # Check for duplicates
    existing_files = list(target_folder.glob(f"{document.file_name}*"))
    if existing_files:
        await message.answer(
            f"File with the same name already exists: {existing_files[0].name}"
        )
        return

    try:
        # Download file
        await bot.download(document, destination=file_path)

        # Parse torrent info
        with open(file_path, "rb") as torrent_file:
            torrent_info = get_torrent_info(torrent_file)

        await message.answer(
            f"File saved: {document.file_name}\n\nTorrent info:\n{torrent_info}"
        )
    except Exception as e:
        logging.error(f"Unable to save file: {e}")
        await message.answer(f"Unable to save file: {document.file_name}")


async def notify_admin(bot: Bot, message: str):
    try:
        current_uptime = get_uptime_message()
        text = message + f"\n\n{current_uptime}"
    except Exception as e:
        logging.error(f"Failed to prepare admin notification text: {e}")
        text = message
    
    if settings.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=settings.ADMIN_IDS[0], text=text)
        except Exception as e:
            logging.error(f"Unable to send message to admin {settings.ADMIN_IDS[0]}: {e}")
