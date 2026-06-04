"""Сборка зависимостей и оркестрация asyncio.

Поднимает Store, ToxService, TelegramService и Router, связывает их колбэками
и держит три сопрограммы в одном цикле: фоновый ``tox.iterate()``, разбор
входящей очереди и поллинг aiogram.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Mapping, Optional

from .config import BridgeConfig
from .models import (
    InboundConnection,
    InboundFileComplete,
    InboundFriendRequest,
    InboundName,
    InboundReadReceipt,
    InboundStatus,
    InboundText,
    InboundTyping,
)
from .router import Router
from .settings import Settings
from .store import Store
from .telegram_service import TelegramCallbacks, TelegramService
from .tox_service import ToxService

log = logging.getLogger(__name__)

_DISPATCH = {
    InboundText: "handle_inbound_text",
    InboundFileComplete: "handle_inbound_file",
    InboundFriendRequest: "handle_inbound_friend_request",
    InboundConnection: "handle_inbound_connection",
    InboundStatus: "handle_inbound_status",
    InboundTyping: "handle_inbound_typing",
    InboundName: "handle_inbound_name",
    InboundReadReceipt: "handle_inbound_read_receipt",
}


async def run(env: Optional[Mapping[str, str]] = None) -> None:
    config = BridgeConfig.from_env(env if env is not None else os.environ)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    store = Store(config.db_path)
    await store.init()

    settings = Settings(store)
    await settings.load()

    owner = config.telegram_owner_id
    if owner is None:
        meta_owner = await store.get_meta("owner_id")
        owner = int(meta_owner) if meta_owner else None

    inbound: asyncio.Queue = asyncio.Queue(maxsize=512)
    tox = ToxService(config, inbound, store, tmp_dir=config.tmp_dir)
    await tox.build()  # требует системный libtoxcore + pytox

    from aiogram import Bot, Dispatcher

    bot = Bot(token=config.telegram_bot_token)

    async def on_outbound_text(thread_id: int, text: str, tg_msg_id: int) -> None:
        await router.handle_outbound_text(thread_id, text, tg_msg_id)

    async def on_outbound_file(thread_id: int, path: str, filename: str) -> None:
        await router.handle_outbound_file(thread_id, path, filename)

    async def on_friend_decision(rid: int, accept: bool) -> None:
        await router.handle_friend_decision(rid, accept)

    async def on_owner_start(owner_id: int) -> None:
        await store.set_meta("owner_id", str(owner_id))
        log.info("owner_id установлен: %s", owner_id)

    async def on_command(name: str, args: str, thread_id: int) -> str:
        return await router.handle_command(name, args, thread_id)

    async def on_settings():
        return router.settings_view()

    async def on_toggle(key: str):
        return await router.toggle_setting(key)

    callbacks = TelegramCallbacks(
        on_outbound_text=on_outbound_text,
        on_outbound_file=on_outbound_file,
        on_friend_decision=on_friend_decision,
        on_owner_start=on_owner_start,
        on_command=on_command,
        on_settings=on_settings,
        on_toggle=on_toggle,
    )
    tg = TelegramService(bot, config, callbacks, tmp_dir=config.tmp_dir)
    if owner is not None:
        tg.set_owner_id(owner)

    router = Router(store, tg, tox, settings)

    async def dispatch(event) -> None:
        method = _DISPATCH.get(type(event))
        if method is None:
            log.warning("неизвестное событие: %r", event)
            return
        await getattr(router, method)(event)

    async def tox_loop() -> None:
        errors = 0
        while True:
            try:
                tox.iterate()
                errors = 0
            except Exception:
                errors += 1
                log.exception("сбой tox.iterate() (подряд: %d)", errors)
            # минимум 20мс, плюс растущая пауза при череде сбоев
            delay = max(tox.iteration_interval, 20) + min(errors, 50) * 100
            await asyncio.sleep(delay / 1000)

    async def consume_inbound() -> None:
        while True:
            event = await inbound.get()
            try:
                await dispatch(event)
            except Exception:
                log.exception("ошибка обработки события")
            finally:
                inbound.task_done()

    dp = Dispatcher()
    tg.register(dp)

    log.info("Tox ID: %s", tox.tox_id)
    if owner is None:
        log.warning("owner_id неизвестен - отправь боту /start в личке Telegram")

    tasks = [asyncio.create_task(tox_loop()), asyncio.create_task(consume_inbound())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        try:
            tox.save()
        except Exception:
            log.exception("не удалось сохранить профиль Tox")
        await store.close()
        await bot.session.close()
