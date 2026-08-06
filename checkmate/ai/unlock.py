"""FIDO-compatible unlock code fetch: API keys in memory only."""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

UNLOCKS_BASE_URL = "https://dl.daisy.org/tools/Fido/unlocks/"
USER_AGENT = "CheckMateUnlock/1.0"
FETCH_TIMEOUT_SEC = 25

_UNLOCK_USER_SETTINGS_KEYS = frozenset(
    {
        "pdf_conversion_method",
        "unified_llm_model",
        "checkmate_model",
        "describer_model",
        "language_slow_model",
        "metadata_model",
        "headings_model",
        "language_model",
    }
)

_UNLOCK_NON_OVERLAY_KEYS = (
    frozenset({"champion", "expires", "require_telemetry"}) | _UNLOCK_USER_SETTINGS_KEYS
)

# In-memory only — never written to disk.
_unlock_api_overlay: dict[str, str] = {}
_unlock_session_model: str | None = None
_last_unlock_payload: dict | None = None


def get_unlock_api_overlay() -> dict[str, str]:
    return dict(_unlock_api_overlay)


def get_unlock_session_model() -> str | None:
    """``Provider: id`` from unlock payload when FIDO user_settings has no model."""
    return _unlock_session_model


def clear_unlock_state() -> None:
    global _unlock_api_overlay, _unlock_session_model, _last_unlock_payload
    _unlock_api_overlay = {}
    _unlock_session_model = None
    _last_unlock_payload = None


def _decode_base64_payload_to_str(text: str) -> str | None:
    cleaned = "".join(text.split())
    if not cleaned:
        return None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            pad = (-len(cleaned)) % 4
            padded = cleaned + ("=" * pad)
            raw = decoder(padded)
            return raw.decode("utf-8")
        except Exception:
            continue
    return None


def parse_unlock_response_body(raw: str) -> dict | None:
    text = raw.strip().lstrip("\ufeff")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    decoded = _decode_base64_payload_to_str(text)
    if decoded is not None:
        try:
            data = json.loads(decoded.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _parse_http_date(header_value: str | None) -> datetime | None:
    if not header_value or not str(header_value).strip():
        return None
    try:
        return parsedate_to_datetime(str(header_value).strip())
    except (TypeError, ValueError):
        return None


def _parse_expires_value(raw: str | None) -> date | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _expires_raw_from_payload(data: dict) -> str | None:
    champ = data.get("champion")
    if isinstance(champ, dict):
        ex = champ.get("expires")
        if ex is not None and str(ex).strip():
            return str(ex).strip()
    ex = data.get("expires")
    if ex is not None and str(ex).strip():
        return str(ex).strip()
    return None


def _is_unlock_expired(server_dt: datetime | None, expires_on: date) -> bool:
    if server_dt is None:
        return False
    try:
        server_d = server_dt.date() if isinstance(server_dt, datetime) else server_dt
    except Exception:
        return False
    return server_d > expires_on


def _fetch_unlock_json(
    unlock_code: str,
) -> tuple[dict | None, str | None, datetime | None]:
    safe = urllib.parse.quote(unlock_code, safe="")
    url = f"{UNLOCKS_BASE_URL}{safe}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            server_dt = _parse_http_date(resp.headers.get("Date"))
            raw = resp.read().decode("utf-8", errors="replace")
        data = parse_unlock_response_body(raw)
        if data is None:
            return None, "bad_json", server_dt
        return data, None, server_dt
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "not_found", None
        logger.info("Unlock fetch HTTP %s", e.code)
        return None, "network", None
    except Exception as e:
        logger.info("Unlock fetch failed: %s", e)
        return None, "network", None


def _apply_overlay(data: dict) -> None:
    global _unlock_api_overlay, _unlock_session_model, _last_unlock_payload
    _unlock_api_overlay = {}
    for k, v in data.items():
        if k in _UNLOCK_NON_OVERLAY_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            _unlock_api_overlay[k] = v
    _last_unlock_payload = data
    unified = data.get("unified_llm_model")
    if isinstance(unified, str) and unified.strip():
        _unlock_session_model = unified.strip()
    else:
        for key in (
            "checkmate_model",
            "describer_model",
            "metadata_model",
            "headings_model",
            "language_model",
        ):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                _unlock_session_model = val.strip()
                break


def refresh_unlock(*, unlock_code: str | None = None) -> dict:
    """
    Fetch unlock JSON and fill in-memory API overlay.

    CheckMate does not enforce FIDO's require_telemetry gate for unlock.
    When the user has opted in via FIDO, anonymous usage events are sent
    through FIDO's activity logger (see ``checkmate.telemetry``).
    Never writes API keys to disk.

    Returns ``{ok: bool, reason: str | None}``.
    """
    from ..fido_settings import get_unlock_code

    code = (unlock_code if unlock_code is not None else get_unlock_code()).strip()
    if not code:
        clear_unlock_state()
        return {"ok": False, "reason": "no_code"}

    data, err, server_dt = _fetch_unlock_json(code)
    if err:
        clear_unlock_state()
        return {"ok": False, "reason": err}

    assert data is not None
    expires_raw = _expires_raw_from_payload(data)
    expires_on = _parse_expires_value(expires_raw) if expires_raw else None
    if expires_on is not None and server_dt is not None:
        if _is_unlock_expired(server_dt, expires_on):
            clear_unlock_state()
            return {"ok": False, "reason": "expired"}

    try:
        _apply_overlay(data)
    except Exception as e:
        logger.exception("Unlock parse error: %s", e)
        clear_unlock_state()
        return {"ok": False, "reason": "parse"}

    return {"ok": True, "reason": None}
