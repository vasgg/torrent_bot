"""Persistent availability history; no external database or monitoring service."""

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.utils import get_uptime_message

HEARTBEAT_SECONDS = 60
OUTAGE_SECONDS = 120

KINDS = {
    "host_restart": "Server reboot (possible power outage)",
    "bot_restart": "Bot restart, server stayed on",
    "unknown_restart": "Bot restart, server boot unknown",
    "clean_stop": "Orderly bot stop, server stayed on",
    "connection": "Connection to Telegram lost",
}


def duration(seconds: float) -> str:
    days, rest = divmod(max(0, int(seconds)), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"


def read_boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


class UptimeStore:
    def __init__(self, path: Path, timezone: str = "Asia/Tbilisi"):
        self.path = path
        self.timezone = ZoneInfo(timezone)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    boot_id TEXT,
                    stopped_at REAL
                );
                CREATE TABLE IF NOT EXISTS outages (
                    id INTEGER PRIMARY KEY,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    kind TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY,
                    message TEXT NOT NULL
                );
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def timestamp(self, value: float) -> str:
        return datetime.fromtimestamp(value, self.timezone).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def online(self, now: float, boot_id: str | None, *, startup: bool = False) -> None:
        """Commit the heartbeat, recovered interval and notification together."""
        with closing(self.connect()) as db, db:
            previous = db.execute("SELECT * FROM state WHERE id = 1").fetchone()
            text = "Bot started.\n"
            if previous is None:
                db.execute(
                    "INSERT INTO state VALUES (1, ?, ?, ?, NULL)",
                    (now, now, boot_id),
                )
                text += "First observation; no previous downtime data."
            else:
                gap = now - previous["last_seen"]
                reboot = bool(
                    boot_id and previous["boot_id"] and boot_id != previous["boot_id"]
                )
                kind = "connection"
                if reboot:
                    kind = "host_restart"
                elif startup:
                    if not boot_id or not previous["boot_id"]:
                        kind = "unknown_restart"
                    elif previous["stopped_at"] is not None:
                        kind = "clean_stop"
                    else:
                        kind = "bot_restart"
                # A clean shutdown is a local event, not proof that Telegram was reachable.
                start = previous["last_seen"]
                if (
                    kind == "clean_stop"
                    and previous["stopped_at"] - start < OUTAGE_SECONDS
                ):
                    start = previous["stopped_at"]
                if now < max(previous["last_seen"], previous["stopped_at"] or 0):
                    logging.warning(
                        "Clock moved backwards; resetting heartbeat without an outage"
                    )
                    text += (
                        "Clock moved backwards; previous downtime cannot be estimated."
                    )
                elif startup or reboot or gap >= OUTAGE_SECONDS:
                    db.execute(
                        "INSERT INTO outages (started_at, ended_at, kind) VALUES (?, ?, ?)",
                        (start, now, kind),
                    )
                    text = ("Bot started." if startup else "Bot is back online.") + "\n"
                    text += self.interval_message(start, now, kind)
                    logging.info(
                        "Availability event: %s, %.0f seconds", kind, now - start
                    )
                db.execute(
                    "UPDATE state SET last_seen = ?, boot_id = ?, stopped_at = NULL WHERE id = 1",
                    (now, boot_id),
                )
            if startup or (
                previous is not None
                and (reboot or now - previous["last_seen"] >= OUTAGE_SECONDS)
            ):
                db.execute("INSERT INTO notifications (message) VALUES (?)", (text,))

    def stopped(self, now: float) -> None:
        with closing(self.connect()) as db, db:
            db.execute("UPDATE state SET stopped_at = ? WHERE id = 1", (now,))

    def interval_message(self, start: float, end: float, kind: str) -> str:
        return (
            f"{KINDS[kind]}\n"
            f"Offline since: ~{self.timestamp(start)}\n"
            f"Back online: {self.timestamp(end)}\n"
            f"Downtime: ~{duration(end - start)} ({self.timezone.key})"
        )

    def pending_notifications(self) -> list[sqlite3.Row]:
        with closing(self.connect()) as db:
            return db.execute(
                "SELECT * FROM notifications ORDER BY id LIMIT 10"
            ).fetchall()

    def notified(self, notification_id: int) -> None:
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))

    def history(self) -> str:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM outages ORDER BY id DESC LIMIT 10"
            ).fetchall()
        if not rows:
            return "No downtime recorded yet."
        return "Last 10 availability events:\n\n" + "\n\n".join(
            self.interval_message(row["started_at"], row["ended_at"], row["kind"])
            for row in rows
        )

    def summary(self, now: float) -> str:
        since = now - 30 * 86400
        with closing(self.connect()) as db:
            state = db.execute("SELECT * FROM state WHERE id = 1").fetchone()
            rows = db.execute(
                "SELECT * FROM outages WHERE ended_at > ? AND started_at < ?",
                (since, now),
            ).fetchall()
        if state is None:
            return "Waiting for the first successful connection to Telegram."
        significant = [
            row for row in rows if row["ended_at"] - row["started_at"] >= OUTAGE_SECONDS
        ]
        total = sum(
            min(now, row["ended_at"]) - max(since, row["started_at"])
            for row in significant
        )
        reboots = sum(row["kind"] == "host_restart" for row in rows)
        lines = [
            f"Tracking since: {self.timestamp(state['first_seen'])} ({self.timezone.key})",
            "Last 30 days (within the observation period):",
            f"Downtimes of at least 2 min: {len(significant)}",
            f"Total downtime: ~{duration(total)}",
            f"Server reboots observed: {reboots} (possible power outages)",
        ]
        for kind in KINDS:
            count = sum(row["kind"] == kind for row in significant)
            if count:
                lines.append(f"{KINDS[kind]}: {count}")
        lines.append(
            "Times are approximate. Power loss cannot be confirmed by the bot alone."
        )
        return "\n".join(lines)


class UptimeMonitor:
    def __init__(self, store: UptimeStore, admin_id: int | None):
        self.store = store
        self.admin_id = admin_id
        self.boot_id = read_boot_id()
        self.ready = asyncio.Event()
        self.started = False

    async def tick(self, bot: Bot) -> None:
        # get_me always makes a request; bot.me caches its result for polling startup.
        await bot.get_me(request_timeout=10)
        if not self.started:
            async with asyncio.timeout(10):
                await bot.me()
        self.store.online(time.time(), self.boot_id, startup=not self.started)
        self.started = True
        self.ready.set()
        if self.admin_id is not None:
            for notification in self.store.pending_notifications():
                await bot.send_message(
                    self.admin_id,
                    notification["message"] + "\n\n" + get_uptime_message(),
                    parse_mode=None,
                    request_timeout=10,
                )
                self.store.notified(notification["id"])

    async def run(self, bot: Bot) -> None:
        while True:
            before = time.monotonic()
            try:
                await self.tick(bot)
            except (TelegramAPIError, OSError, TimeoutError) as exc:
                # Exception strings from HTTP clients can contain token-bearing URLs.
                logging.warning(
                    "Availability check/notification failed: %s", type(exc).__name__
                )
            await asyncio.sleep(max(0, HEARTBEAT_SECONDS - (time.monotonic() - before)))
