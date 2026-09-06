import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from src.uptime import UptimeMonitor, UptimeStore, duration


@pytest.fixture
def store(tmp_path):
    return UptimeStore(tmp_path / "state" / "uptime.sqlite3")


def events(store):
    with closing(store.connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM outages ORDER BY id")]


def test_first_start_and_regular_heartbeats(store):
    store.online(1000, "boot-a", startup=True)
    store.online(1060, "boot-a")
    store.online(1120, "boot-a")
    assert events(store) == []
    assert "First observation" in store.pending_notifications()[0]["message"]
    assert "Downtimes of at least 2 min: 0" in store.summary(1120)


def test_power_loss_survives_reopening_database(store):
    store.online(1000, "boot-a", startup=True)
    store.online(1060, "boot-a")
    reopened = UptimeStore(store.path)
    reopened.online(4660, "boot-b", startup=True)
    reopened.online(4720, "boot-b")
    [event] = events(reopened)
    assert event["kind"] == "host_restart"
    assert (event["started_at"], event["ended_at"]) == (1060, 4660)
    assert "1h 0m 0s" in reopened.pending_notifications()[-1]["message"]
    assert "Server reboots observed: 1" in reopened.summary(4720)


def test_connection_loss_without_process_restart(store):
    store.online(1000, "boot-a", startup=True)
    store.online(1600, "boot-a")
    store.online(1660, "boot-a")
    assert [row["kind"] for row in events(store)] == ["connection"]
    assert "back online" in store.pending_notifications()[-1]["message"]


def test_short_network_gap_is_ignored(store):
    store.online(1000, "boot-a", startup=True)
    store.online(1119, "boot-a")
    assert events(store) == []


def test_clean_restart_is_not_a_power_outage(store):
    store.online(1000, "boot-a", startup=True)
    store.stopped(1040)
    store.online(1050, "boot-a", startup=True)
    [event] = events(store)
    assert event["kind"] == "clean_stop"
    assert event["started_at"] == 1040
    assert "Downtimes of at least 2 min: 0" in store.summary(1060)
    assert "Server reboots observed: 0" in store.summary(1060)
    assert "10s" in store.history()


def test_orderly_shutdown_followed_by_host_reboot(store):
    store.online(1000, "boot-a", startup=True)
    store.stopped(1040)
    store.online(1600, "boot-b", startup=True)
    assert events(store)[0]["kind"] == "host_restart"


def test_shutdown_during_network_outage_preserves_last_connection(store):
    store.online(1000, "boot-a", startup=True)
    store.stopped(1600)
    store.online(2200, "boot-a", startup=True)
    assert events(store)[0]["started_at"] == 1000


@pytest.mark.parametrize(
    "boot_id,kind", [("boot-a", "bot_restart"), (None, "unknown_restart")]
)
def test_process_crash_classification(store, boot_id, kind):
    store.online(1000, boot_id, startup=True)
    store.online(1600, boot_id, startup=True)
    assert events(store)[0]["kind"] == kind


def test_backwards_clock_does_not_create_negative_outage(store):
    store.online(1000, "boot-a", startup=True)
    store.online(900, "boot-a", startup=True)
    store.online(960, "boot-a")
    assert events(store) == []
    assert "Clock moved backwards" in store.pending_notifications()[-1]["message"]


def test_summary_clips_intervals_to_last_30_days(store):
    now = 40 * 86400
    cutoff = now - 30 * 86400
    store.online(cutoff - 3600, "boot-a", startup=True)
    store.online(cutoff + 3600, "boot-b", startup=True)
    assert "Total downtime: ~0d 1h 0m 0s" in store.summary(now)
    assert "Downtimes of at least 2 min: 1" in store.summary(now)


def test_history_is_bounded_and_timezone_is_local(store):
    for i in range(15):
        store.online(i * 1000, "boot-a", startup=True)
    history = store.history()
    assert history.count("Offline since:") == 10
    assert "Asia/Tbilisi" in history
    assert store.timestamp(0) == "1970-01-01 04:00:00"
    assert len(history) < 4096


def test_corrupt_database_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "uptime.sqlite3"
    original = b"not a database"
    path.write_bytes(original)
    with pytest.raises(sqlite3.DatabaseError):
        UptimeStore(path)
    assert path.read_bytes() == original


async def test_monitor_does_not_heartbeat_offline_and_recovers(store):
    store.online(1000, "boot-a", startup=True)
    monitor = UptimeMonitor(store, 123)
    monitor.boot_id = "boot-b"
    bot = AsyncMock()
    bot.get_me.side_effect = TelegramNetworkError(GetMe(), "offline")
    with pytest.raises(TelegramNetworkError):
        await monitor.tick(bot)
    assert not monitor.ready.is_set()
    assert events(store) == []
    bot.get_me.side_effect = None
    with patch("src.uptime.time.time", return_value=4600):
        await monitor.tick(bot)
    assert monitor.ready.is_set()
    assert events(store)[0]["ended_at"] == 4600
    assert len(store.pending_notifications()) == 0


async def test_notification_retried_without_duplicate_outage(store):
    store.online(1000, "boot-a", startup=True)
    for row in store.pending_notifications():
        store.notified(row["id"])
    monitor = UptimeMonitor(store, 123)
    monitor.boot_id = "boot-b"
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramNetworkError(GetMe(), "offline")
    with patch("src.uptime.time.time", return_value=4600):
        with pytest.raises(TelegramNetworkError):
            await monitor.tick(bot)
    assert monitor.ready.is_set()
    assert len(events(store)) == 1
    assert len(UptimeStore(store.path).pending_notifications()) == 1
    bot.send_message.side_effect = None
    with patch("src.uptime.time.time", return_value=4660):
        await monitor.tick(bot)
    assert len(events(store)) == 1
    assert not store.pending_notifications()
    assert "1h 0m 0s" in bot.send_message.call_args.args[1]


async def test_monitor_cancellation_propagates(store):
    monitor = UptimeMonitor(store, 123)
    bot = AsyncMock()
    bot.get_me.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await monitor.run(bot)
    assert not monitor.started


def test_duration():
    assert duration(90061) == "1d 1h 1m 1s"
    assert duration(-1) == "0d 0h 0m 0s"
