"""Точка входа: python -m bridge."""

from __future__ import annotations

import asyncio
import sys

from .app import run
from .config import ConfigError
from .tox_service import ProfileDecryptError


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except ProfileDecryptError as exc:
        print(f"Ошибка профиля: {exc}", file=sys.stderr)
        raise SystemExit(4)
    except ImportError as exc:
        print(
            "Не удалось загрузить py-toxcore-c/libtoxcore. "
            "Запусти через Docker или установи системный libtoxcore. "
            f"Детали: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(3)
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
