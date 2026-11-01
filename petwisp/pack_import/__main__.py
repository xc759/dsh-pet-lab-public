# -*- coding: utf-8 -*-
"""``python -m petwisp.pack_import <子命令>`` 的入口薄壳。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":          # pragma: no cover - 入口
    raise SystemExit(main())
