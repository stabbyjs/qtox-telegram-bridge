"""Настройки моста, управляемые прямо из чата с ботом.

Переключатели и список заглушённых контактов держатся в памяти для быстрых
проверок в горячем пути и дублируются в Store, чтобы пережить рестарт.
"""

from __future__ import annotations


class Settings:
    DEFAULTS: dict[str, bool] = {
        "notify_status": True,
        "notify_typing": True,
        "read_receipts": True,
        "forward_files": True,
        "show_actions": True,
        "silent": False,
    }

    LABELS: dict[str, str] = {
        "notify_status": "Уведомления о статусах",
        "notify_typing": "Индикатор набора текста",
        "read_receipts": "Отметки о прочтении",
        "forward_files": "Приём файлов",
        "show_actions": "Действия /me",
        "silent": "Беззвучные сообщения",
    }

    ORDER = [
        "notify_status",
        "notify_typing",
        "read_receipts",
        "forward_files",
        "show_actions",
        "silent",
    ]

    def __init__(self, store):
        self._store = store
        self._toggles: dict[str, bool] = dict(self.DEFAULTS)
        self._muted: set[str] = set()

    async def load(self) -> None:
        for key in self.DEFAULTS:
            raw = await self._store.get_meta(f"set:{key}")
            if raw is not None:
                self._toggles[key] = raw == "1"
        muted = await self._store.get_meta("muted")
        if muted:
            self._muted = {p for p in muted.split(",") if p}

    def get(self, key: str) -> bool:
        return self._toggles.get(key, self.DEFAULTS.get(key, False))

    async def set(self, key: str, value: bool) -> None:
        if key not in self.DEFAULTS:
            return
        self._toggles[key] = bool(value)
        await self._store.set_meta(f"set:{key}", "1" if value else "0")

    async def toggle(self, key: str) -> bool:
        await self.set(key, not self.get(key))
        return self.get(key)

    def is_muted(self, pubkey: str) -> bool:
        return pubkey in self._muted

    async def mute(self, pubkey: str) -> None:
        self._muted.add(pubkey)
        await self._persist_muted()

    async def unmute(self, pubkey: str) -> None:
        self._muted.discard(pubkey)
        await self._persist_muted()

    async def _persist_muted(self) -> None:
        await self._store.set_meta("muted", ",".join(sorted(self._muted)))

    def view(self) -> list[tuple[str, str, bool]]:
        """(ключ, подпись, включено) в порядке отображения в меню."""
        return [(key, self.LABELS[key], self.get(key)) for key in self.ORDER]
