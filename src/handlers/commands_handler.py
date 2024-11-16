from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.internal.config import Settings
from src.internal.lexicon import texts

router = Router()


@router.message(CommandStart)
async def command_handler(
    message: Message,
    settings: Settings,
) -> None:
    if message.from_user.id not in settings.ADMINS:
        return

    await message.answer(
        text=texts['welcome'].format(username=message.from_user.full_name),
    )
