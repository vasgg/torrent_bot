import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from torrent_parser import TorrentFileParser

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Document


async def save_torrent(bot: "Bot", document: "Document", target: Path) -> bool:
    # Watch folders must only see complete torrents, including after a power loss.
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part") as staging:
        await bot.download(document, destination=staging)
        staging.flush()
        os.fsync(staging.fileno())
        try:
            os.link(staging.name, target)
        except FileExistsError:
            return False
    return True


def get_uptime_message() -> str:
    try:
        with open("/proc/uptime") as f:
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
    name = data["info"].get("name", "Unknown")
    files = data["info"].get("files", [])
    total_size = (
        sum(f["length"] for f in files) if files else data["info"].get("length", 0)
    )
    formatted_files = (
        "\n".join(
            [f"- {f['path'][0]} ({f['length'] / (1024 * 1024):.2f} MB)" for f in files]
        )
        if files
        else f"- {name} ({total_size / (1024 * 1024):.2f} MB)"
    )

    return f"File name: {name}\nTotal size: {total_size / (1024 * 1024):.2f} MB\nFiles:\n{formatted_files}"
