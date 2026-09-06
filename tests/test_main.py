import asyncio
import signal
from contextlib import closing

import pytest
from aiogram import Bot, types
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, GetUpdates, SendMessage

from src import main
from src.uptime import UptimeStore


@pytest.mark.parametrize("online", [False, True])
async def test_shutdown_before_and_after_polling(tmp_path, monkeypatch, online):
    path = tmp_path / "uptime.sqlite3"
    monkeypatch.setattr(main.settings, "UPTIME_DB", path)
    signal_handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda sig, callback: signal_handlers.update({sig: callback}),
    )
    monkeypatch.setattr(
        loop, "remove_signal_handler", lambda sig: signal_handlers.pop(sig)
    )
    reached = asyncio.Event()
    polling_started = False

    async def request(self, method, **kwargs):
        nonlocal polling_started
        if isinstance(method, GetMe):
            if not online:
                reached.set()
                raise TelegramNetworkError(method, "offline")
            return types.User(
                id=123456, is_bot=True, first_name="Test", username="test_bot"
            )
        if isinstance(method, GetUpdates):
            polling_started = True
            reached.set()
            await asyncio.Future()
        if isinstance(method, SendMessage):
            return None
        raise AssertionError(type(method))

    monkeypatch.setattr(Bot, "__call__", request)
    task = asyncio.create_task(main.main())
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        signal_handlers[signal.SIGTERM]()
        await asyncio.wait_for(task, timeout=5)
        assert polling_started == online
        with closing(UptimeStore(path).connect()) as db:
            state = db.execute("SELECT * FROM state").fetchone()
        if online:
            assert state["stopped_at"] >= state["last_seen"]
        else:
            assert state is None
        assert not signal_handlers
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        main.router._parent_router = None
