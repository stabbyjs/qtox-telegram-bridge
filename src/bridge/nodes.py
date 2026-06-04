"""Bootstrap-ноды Tox DHT.

Статичный fallback-набор публичных нод. Актуальный список можно получить с
https://nodes.tox.chat/json - при желании заменить значения ниже.
Формат: (host, port, public_key_hex).
"""

from __future__ import annotations

BOOTSTRAP_NODES: list[tuple[str, int, str]] = [
    ("tox.plastiras.org", 33445, "8E8B63299B3D520FB377FE5100E65E3322F7AE5B20A0ACED2981769FC5B43725"),
    ("tox.abilinski.com", 33445, "10C00EB250C3233E343E2AEBA07115A5C28920E9C8D29492F6D00B29049EDC7C"),
    ("198.199.98.108", 33445, "BEF0CFB37AF874BD17B9A8F9FE64C75521DB95A37D33C5BDB00E9CF58659C04F"),
    ("172.105.109.31", 33445, "D46E97CF995DC1820B92B7D899E152A217D36ABE22730FEA4B6BF1BB5C627527"),
    ("tox.initramfs.io", 33445, "3F0A45A268367C1BEA652F258C85F4A66DA76BCAA667A49E770BCC4917AB6A25"),
]
