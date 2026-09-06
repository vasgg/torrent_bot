import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties

from src import handlers
from src.uptime import UptimeStore
from src.utils import save_torrent


@pytest.fixture
def document():
    return types.Document(
        file_id="file", file_unique_id="unique", file_name="test.torrent"
    )


async def test_failed_download_never_publishes_partial_torrent(tmp_path, document):
    target = tmp_path / "test.torrent"

    async def download(document, destination):
        destination.write(b"partial")
        assert not target.exists()
        raise OSError("connection lost")

    with pytest.raises(OSError):
        await save_torrent(SimpleNamespace(download=download), document, target)
    assert list(tmp_path.iterdir()) == []


async def test_download_publishes_complete_file_and_does_not_overwrite(
    tmp_path, document
):
    target = tmp_path / "test.torrent"

    async def download(document, destination):
        destination.write(b"complete")

    bot = SimpleNamespace(download=download)
    assert await save_torrent(bot, document, target)
    assert target.read_bytes() == b"complete"
    target.write_bytes(b"existing")
    assert not await save_torrent(bot, document, target)
    assert target.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [target]


async def test_cancelled_download_cleans_staging_file(tmp_path, document):
    bot = AsyncMock()
    bot.download.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await save_torrent(bot, document, tmp_path / "test.torrent")
    assert list(tmp_path.iterdir()) == []


async def test_double_click_processes_batch_once(tmp_path, monkeypatch, document):
    monkeypatch.setattr(handlers.settings, "TORRENT_DIR", str(tmp_path))
    batch = handlers.PendingBatch(
        123, 123, "test", [document], created_at_monotonic=handlers.time.monotonic()
    )
    monkeypatch.setattr(handlers, "_pending_batches", {"test": batch})
    callback = AsyncMock()
    callback.data = "tclass|test|movies"
    callback.from_user.id = 123
    bot = AsyncMock()
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers, "save_torrent", save)
    await asyncio.gather(
        handlers.classify_batch(callback, bot), handlers.classify_batch(callback, bot)
    )
    assert save.await_count == 1


async def test_commands_are_admin_only_and_receive_store(tmp_path):
    store = UptimeStore(tmp_path / "uptime.sqlite3")
    store.online(1000, "boot-a", startup=True)
    dp = Dispatcher(uptime_store=store)
    dp.include_router(handlers.router)
    bot = Bot("123456:TEST_TOKEN", default=DefaultBotProperties(parse_mode="HTML"))
    bot.session = AsyncMock()
    try:
        for command in ("/uptime", "/outages", "/start"):
            for user_id in (999, 123):
                bot.session.reset_mock()
                update = types.Update(
                    update_id=1,
                    message=types.Message(
                        message_id=1,
                        date=datetime.now(timezone.utc),
                        chat=types.Chat(id=user_id, type="private"),
                        from_user=types.User(
                            id=user_id, is_bot=False, first_name="A < B"
                        ),
                        text=command,
                        entities=[
                            types.MessageEntity(
                                type="bot_command", offset=0, length=len(command)
                            )
                        ],
                    ),
                )
                await dp.feed_update(bot, update)
                assert bot.session.call_count == (1 if user_id == 123 else 0)
                if command == "/start" and user_id == 123:
                    assert "A &lt; B" in bot.session.call_args.args[1].text
    finally:
        handlers.router._parent_router = None
        await bot.session.close()


def test_filename_cannot_escape_destination():
    assert handlers._safe_torrent_filename("../../foo.torrent") == "foo.torrent"
    assert handlers._safe_torrent_filename("/tmp/foo.torrent") == "foo.torrent"


async def test_expired_callback_still_saves_confirmed_files(
    tmp_path, monkeypatch, document
):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import AnswerCallbackQuery

    monkeypatch.setattr(handlers.settings, "TORRENT_DIR", str(tmp_path))
    batch = handlers.PendingBatch(
        123, 123, "test", [document], created_at_monotonic=handlers.time.monotonic()
    )
    monkeypatch.setattr(handlers, "_pending_batches", {"test": batch})
    callback = AsyncMock()
    callback.data = "tclass|test|movies"
    callback.from_user.id = 123
    callback.answer.side_effect = TelegramBadRequest(
        AnswerCallbackQuery(callback_query_id="old"), "query is too old"
    )
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers, "save_torrent", save)
    await handlers.classify_batch(callback, AsyncMock())
    assert save.await_count == 1
