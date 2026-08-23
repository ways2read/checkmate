"""Breadcrumbs for modal/WebView close hangs. Search logs for ``[dlg-trace]``."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("checkmate")


def dlg_trace(where: str, win: Any = None, **extra: Any) -> None:
    """Log a close-hang breadcrumb (same idea as Fido ``dlg_trace``)."""
    bits = [f"[dlg-trace] {where}"]
    if win is not None:
        try:
            bits.append(type(win).__name__)
            bits.append(f"oid={id(win)}")
            bits.append(f"shown={bool(win.IsShown())}")
            try:
                bits.append(f"modal={bool(win.IsModal())}")
            except Exception:
                bits.append("modal=?")
            parent = win.GetParent()
            bits.append(f"parent={type(parent).__name__ if parent else None}")
        except Exception as exc:
            bits.append(f"win_err={exc}")
    for key, value in extra.items():
        bits.append(f"{key}={value}")
    _log.info(" ".join(str(part) for part in bits))
