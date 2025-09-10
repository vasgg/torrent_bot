import logging
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
from torrent_parser import TorrentFileParser

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import Settings, setup_logs
from lexicon import texts

settings = Settings()

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".uptime_state.json"


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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_boot_id() -> str:
    try:
        with open('/proc/sys/kernel/random/boot_id', 'r') as f:
            return f.read().strip()
    except Exception:
        try:
            with open('/proc/uptime') as f:
                uptime_seconds = float(f.readline().split()[0])
            boot_start = int((datetime.now(timezone.utc).timestamp() - uptime_seconds))
            return f"pseudo-{boot_start}"
        except Exception:
            return "unknown"


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as e:
        logging.error(f"Failed to load state file: {e}")
    return {"sessions": {}}


def _atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content)
        os.replace(tmp_path, path)
    except Exception as e:
        logging.error(f"Failed to write state file atomically: {e}")
        try:
            path.write_text(content)
        except Exception as e2:
            logging.error(f"Failed to write state file (fallback): {e2}")


def _save_state(state: Dict[str, Any]) -> None:
    _atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))


def record_session_heartbeat() -> None:
    state = _load_state()
    sessions = state.setdefault("sessions", {})
    boot_id = _read_boot_id()
    now_iso = _now_utc_iso()

    if boot_id not in sessions:
        sessions[boot_id] = {
            "start": now_iso,
            "last_seen": now_iso,
        }
    else:
        sessions[boot_id]["last_seen"] = now_iso

    _save_state(state)


def get_last_session_uptime_message() -> str:
    try:
        state = _load_state()
        sessions: Dict[str, Dict[str, str]] = state.get("sessions", {})
        if not sessions:
            return "No previous session data yet."

        current_boot = _read_boot_id()
        entries = [
            (bid, s) for bid, s in sessions.items() if bid != current_boot and "start" in s and "last_seen" in s
        ]
        if not entries:
            return "No previous session recorded yet."

        entries.sort(key=lambda x: x[1].get("last_seen", ""), reverse=True)
        _, prev = entries[0]

        start_dt = datetime.fromisoformat(prev["start"]) if "start" in prev else None
        end_dt = datetime.fromisoformat(prev["last_seen"]) if "last_seen" in prev else None
        if not start_dt or not end_dt:
            return "Previous session data is incomplete."

        delta = end_dt - start_dt
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, _ = divmod(rem, 60)

        return (
            f"Previous session uptime until shutdown: {days} days, {hours} hours, {minutes} minutes.\n"
            f"Last shutdown (approx): {end_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} UTC."
        )
    except Exception as e:
        logging.error(f"Unable to compute previous session uptime: {e}")
        return "Unable to compute previous session uptime."


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
        await bot.send_message(
            chat_id=settings.ADMINS[0],
            text=message + f"\n\n{current_uptime}",
        )
    except Exception as e:
        logging.error(f"Unable to send message to admin {settings.ADMINS[0]}: {e}")


async def _heartbeat_job(context):
    try:
        record_session_heartbeat()
    except Exception as e:
        logging.error(f"Heartbeat update failed: {e}")


async def on_startup(app):
    record_session_heartbeat()
    try:
        app.job_queue.run_repeating(_heartbeat_job, interval=60, first=60)
    except Exception as e:
        logging.error(f"Failed to schedule heartbeat job: {e}")

    await notify_admin(app.bot, "Bot started.")
    logging.info("Bot started.")


def main():
    setup_logs('torrent_bot')
    app = Application.builder().token(settings.TOKEN.get_secret_value()).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    app.post_init = on_startup
    app.run_polling()


if __name__ == '__main__':
    main()
