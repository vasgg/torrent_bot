import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import settings
from src.uptime import UptimeStore
from src.utils import get_torrent_info, get_uptime_message, save_torrent

router = Router()

BatchType = Literal["movies", "series"]

_BATCH_DEBOUNCE_SECONDS = 1.0
_BATCH_TTL_SECONDS = 60 * 60


@dataclass
class PendingBatch:
    chat_id: int
    owner_user_id: int
    group_key: str
    files: list[types.Document]
    prompt_message_id: int | None = None
    prompt_task: asyncio.Task[None] | None = None
    created_at_monotonic: float = 0.0
    last_update_monotonic: float = 0.0


_pending_batches: dict[str, PendingBatch] = {}


def _cleanup_expired_batches() -> None:
    now = time.monotonic()
    expired_keys: list[str] = []
    for key, batch in _pending_batches.items():
        if now - batch.created_at_monotonic > _BATCH_TTL_SECONDS:
            expired_keys.append(key)

    for key in expired_keys:
        batch = _pending_batches.pop(key, None)
        if batch and batch.prompt_task and not batch.prompt_task.done():
            batch.prompt_task.cancel()


def _build_batch_keyboard(group_key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 Movies", callback_data=f"tclass|{group_key}|movies")
    kb.button(text="📺 Series", callback_data=f"tclass|{group_key}|series")
    kb.button(text="✖️ Cancel", callback_data=f"tclass|{group_key}|cancel")
    kb.adjust(2, 1)
    return kb.as_markup()


def _prompt_text(file_count: int) -> str:
    if file_count == 1:
        return "Got 1 .torrent file. Where should I put it?"
    return f"Got {file_count} .torrent files. Where should I put this batch?"


def _safe_torrent_filename(file_name: str) -> str:
    return Path(file_name).name


def _dest_subdir(content_type: BatchType) -> str:
    return "Movies" if content_type == "movies" else "Series"


@router.message(CommandStart(), F.from_user.id.in_(settings.ADMIN_IDS))
async def cmd_start(message: Message) -> None:
    uptime = get_uptime_message()
    text = (
        f"Hi, {escape(message.from_user.full_name)}.\n\n"
        "Send .torrent files (single or batch).\n\n"
        "/uptime — availability summary\n"
        "/outages — recent downtime\n"
        "/health — host healthcheck\n\n"
        f"{uptime}"
    )
    await message.answer(text)


@router.message(F.document, F.from_user.id.in_(settings.ADMIN_IDS))
async def handle_torrent_file(message: Message, bot: Bot) -> None:
    document = message.document
    file_name = document.file_name or ""
    if not file_name.lower().endswith(".torrent"):
        await message.answer("Only .torrent files are supported.")
        return

    _cleanup_expired_batches()

    if message.media_group_id:
        group_key = f"mg:{message.chat.id}:{message.media_group_id}"
    else:
        group_key = f"msg:{message.chat.id}:{message.message_id}"

    now = time.monotonic()
    batch = _pending_batches.get(group_key)
    if not batch:
        batch = PendingBatch(
            chat_id=message.chat.id,
            owner_user_id=message.from_user.id,
            group_key=group_key,
            files=[],
            created_at_monotonic=now,
            last_update_monotonic=now,
        )
        _pending_batches[group_key] = batch
    else:
        batch.last_update_monotonic = now

    batch.files.append(document)

    if batch.prompt_task and not batch.prompt_task.done():
        batch.prompt_task.cancel()

    batch.prompt_task = asyncio.create_task(_send_batch_prompt(bot, batch.group_key))

    if batch.prompt_message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=batch.chat_id,
                message_id=batch.prompt_message_id,
                text=_prompt_text(len(batch.files)),
                reply_markup=_build_batch_keyboard(batch.group_key),
            )
        except Exception:
            pass


async def _send_batch_prompt(bot: Bot, group_key: str) -> None:
    await asyncio.sleep(_BATCH_DEBOUNCE_SECONDS)
    batch = _pending_batches.get(group_key)
    if not batch:
        return
    if batch.prompt_message_id is not None:
        return

    try:
        msg = await bot.send_message(
            chat_id=batch.chat_id,
            text=_prompt_text(len(batch.files)),
            reply_markup=_build_batch_keyboard(batch.group_key),
        )
        batch.prompt_message_id = msg.message_id
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.error("Unable to send batch prompt: %s", type(e).__name__)
        _pending_batches.pop(group_key, None)


@router.callback_query(
    F.data.startswith("tclass|"), F.from_user.id.in_(settings.ADMIN_IDS)
)
async def classify_batch(callback: types.CallbackQuery, bot: Bot) -> None:
    _cleanup_expired_batches()

    parts = (callback.data or "").split("|", 2)
    if len(parts) != 3:
        await callback.answer("Invalid action.", show_alert=True)
        return

    _, group_key, action = parts
    batch = _pending_batches.get(group_key)
    if not batch:
        await callback.answer(
            "This batch is already processed or expired.", show_alert=True
        )
        try:
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if callback.from_user.id != batch.owner_user_id:
        await callback.answer(
            "This batch must be confirmed by the sender.", show_alert=True
        )
        return

    if action == "cancel":
        _pending_batches.pop(group_key, None)
        await callback.answer("Canceled.")
        if callback.message:
            try:
                await callback.message.edit_text("Canceled.", reply_markup=None)
            except Exception:
                pass
        return

    if action not in {"movies", "series"}:
        await callback.answer("Unknown action.", show_alert=True)
        return

    # Claim before the first await so a double click cannot process this batch twice.
    _pending_batches.pop(group_key, None)
    if batch.prompt_task and not batch.prompt_task.done():
        batch.prompt_task.cancel()
    try:
        await callback.answer("Saving...")
    except TelegramAPIError as exc:
        # Telegram can expire a callback while the server is offline.
        logging.warning("Unable to acknowledge batch: %s", type(exc).__name__)

    dest_dir = Path(settings.TORRENT_DIR) / _dest_subdir(action)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.error(f"Unable to create destination dir {dest_dir}: {e}")
        if callback.message:
            await callback.message.answer(
                "Can't create destination folder. Please send the files again."
            )
        return

    saved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    for document in batch.files:
        safe_name = _safe_torrent_filename(document.file_name or "")
        if not safe_name:
            errors.append("<empty>")
            continue

        if safe_name in seen_names:
            skipped.append(safe_name)
            continue
        seen_names.add(safe_name)

        target_path = dest_dir / safe_name
        if target_path.exists():
            skipped.append(safe_name)
            continue

        try:
            if await save_torrent(bot, document, target_path):
                saved.append(safe_name)
            else:
                skipped.append(safe_name)
        except Exception as e:
            logging.error("Unable to save file %s: %s", safe_name, type(e).__name__)
            errors.append(safe_name)

    if callback.message:
        try:
            pretty_type = _dest_subdir(action)
            await callback.message.edit_text(
                f"{pretty_type}: saved {len(saved)}, skipped {len(skipped)} (duplicates), errors {len(errors)}.",
                reply_markup=None,
            )
        except Exception:
            pass

    if callback.message:
        try:
            lines: list[str] = [
                f"Folder: {dest_dir}",
                f"Saved: {len(saved)}",
                f"Skipped (duplicates): {len(skipped)}",
            ]
            if errors:
                lines.append(f"Errors: {len(errors)}")

            if len(batch.files) == 1 and saved:
                file_path = dest_dir / saved[0]
                with open(file_path, "rb") as torrent_file:
                    torrent_info = get_torrent_info(torrent_file)
                lines.append("")
                lines.append("Torrent info:")
                lines.append(torrent_info)

            await callback.message.answer("\n".join(lines)[:4000], parse_mode=None)
        except Exception as e:
            logging.error("Unable to send summary: %s", type(e).__name__)


@router.message(Command("uptime"), F.from_user.id.in_(settings.ADMIN_IDS))
async def cmd_uptime(message: Message, uptime_store: UptimeStore) -> None:
    await message.answer(
        get_uptime_message() + "\n\n" + uptime_store.summary(time.time()),
        parse_mode=None,
    )


@router.message(Command("outages"), F.from_user.id.in_(settings.ADMIN_IDS))
async def cmd_outages(message: Message, uptime_store: UptimeStore) -> None:
    await message.answer(uptime_store.history(), parse_mode=None)


@router.message(Command("health"), F.from_user.id.in_(settings.ADMIN_IDS))
async def cmd_health(message: Message):
    trigger_path = Path("/triggers/health.run")
    trigger_path.parent.mkdir(parents=True, exist_ok=True)

    trigger_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

    await message.answer(
        "<pre>/health accepted. Running host healthcheck...</pre>",
        parse_mode="HTML",
    )
