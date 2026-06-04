"""Сборка и выдача файлов для Tox file transfer.

Tox передаёт файлы кусками: входящий собирается из пар (position, data),
исходящий отдаётся по запросам (position, length). Здесь только работа с
диском - про сам toxcore эти классы не знают.
"""

from __future__ import annotations

import os
import re
import uuid

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_UNSAFE_RE = re.compile(r"[^\w.\-]", re.UNICODE)


class TransferTooLarge(Exception):
    """Входящий файл превысил допустимый лимит."""


def is_image(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in _IMAGE_EXTS


def safe_filename(raw: str) -> str:
    """Очистить имя файла из недоверенного источника.

    Срезает путь, заменяет небезопасные символы и ведущие точки. Без этого
    собеседник мог бы прислать имя с ``..`` и записать файл куда угодно.
    """
    name = _UNSAFE_RE.sub("_", os.path.basename(raw or "")).lstrip(".")
    return (name or "file.bin")[:200]


def unique_path(tmp_dir: str, filename: str) -> str:
    """Уникальный путь во временном каталоге.

    Короткий случайный префикс не даёт файлам от разных собеседников
    затирать друг друга (например, два ``photo.jpg``).
    """
    return os.path.join(tmp_dir, f"{uuid.uuid4().hex[:12]}_{safe_filename(filename)}")


class IncomingTransfer:
    """Принимает входящий файл и пишет его на диск.

    ``size == 0`` означает, что размер заранее неизвестен - тогда конец
    обозначается пустым куском ``feed(pos, b"")``.
    """

    def __init__(self, size: int, tmp_path: str, max_bytes: int):
        self.size = size
        self.tmp_path = tmp_path
        self.max_bytes = max_bytes
        self.received = 0
        self._complete = False
        os.makedirs(os.path.dirname(tmp_path) or ".", exist_ok=True)
        self._fh = open(tmp_path, "wb")

    def feed(self, position: int, data: bytes) -> None:
        if not data:
            self._complete = True
            return
        # position приходит из сети - без проверки границ собеседник мог бы
        # одним байтом на смещении 2**42 создать разреженный файл на терабайты.
        if position < 0 or position + len(data) > self.max_bytes:
            self.close()
            raise TransferTooLarge(f"файл превышает лимит {self.max_bytes} байт")
        if self.size and position + len(data) > self.size:
            self.close()
            raise TransferTooLarge("кусок выходит за объявленный размер файла")
        self._fh.seek(position)
        self._fh.write(data)
        self.received += len(data)
        if self.size and self.received >= self.size:
            self._complete = True

    @property
    def is_complete(self) -> bool:
        return self._complete

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def finish(self) -> None:
        self.close()

    def cleanup(self) -> None:
        self.close()
        try:
            os.remove(self.tmp_path)
        except FileNotFoundError:
            pass


class OutgoingTransfer:
    """Читает файл и отдаёт его кусками по запросам toxcore."""

    def __init__(self, path: str):
        self.path = path
        self.size = os.path.getsize(path)
        self._fh = open(path, "rb")

    def read_chunk(self, position: int, length: int) -> bytes:
        if length <= 0:
            return b""
        self._fh.seek(position)
        return self._fh.read(length)

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()
