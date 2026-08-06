"""Anonymous usage telemetry aligned with FIDO (shared consent + secrets).

CheckMate does **not** import the FIDO package. It reads the same opt-in and
credentials FIDO uses, then sends events itself:

* Consent: ``telemetry_consent`` in FIDO's ``user_settings.json`` (set in FIDO)
* Secrets: ``fido.secrets.json`` (bundled with CheckMate builds / beside the exe)
  or ``FIDO_OPENPANEL_*`` / ``FIDO_POSTHOG_*`` environment variables
* Local counters: FIDO's ``last_run.json`` (CheckMate-specific keys)

If consent is missing/false, or no provider credentials exist, calls are no-ops
(except local ``last_run`` counter updates when FIDO app-data is writable).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_APP_KIND = "checkmate"
_TELEMETRY_CONSENT_SETTING = "telemetry_consent"
_LAST_STARTED_VERSION_KEY = "last_started_checkmate_version"
_APP_START_TOTAL_KEY = "checkmate_app_start_total"
_INSTALL_ID_KEY = "installId"
_SECRETS_BASENAME = "fido.secrets.json"
_DEFAULT_OPENPANEL_URL = "https://api.openpanel.dev"

_LAST_RUN_LOCK = threading.Lock()
_SECRETS_CACHE: dict[str, Any] | None = None
_PROCESS_LOGGER: Any = None


def _checkmate_version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:
        return ""


def _fido_app_data_dir() -> Path:
    from .fido_settings import fido_app_data_dir

    return fido_app_data_dir()


def is_telemetry_consent_granted() -> bool:
    """True only when FIDO's ``telemetry_consent`` is explicitly ``True``."""
    try:
        from .fido_settings import get_user_setting

        return get_user_setting(_TELEMETRY_CONSENT_SETTING) is True
    except Exception:
        return False


def telemetry_available() -> bool:
    """True when consent is granted and at least one analytics backend is configured."""
    if not is_telemetry_consent_granted():
        return False
    op_id, op_secret, _ = _resolve_openpanel_credentials()
    if op_id and op_secret:
        return True
    ph_token, _ = _resolve_posthog_credentials()
    return bool(ph_token)


# --- last_run.json (FIDO app-data) -------------------------------------------------


def _last_run_path() -> Path:
    return _fido_app_data_dir() / "last_run.json"


def _read_last_run() -> dict[str, Any]:
    path = _last_run_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _write_last_run(data: dict[str, Any]) -> None:
    path = _last_run_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.debug("Could not write %s", path, exc_info=True)


def _last_run_get(key: str, default: Any = None) -> Any:
    with _LAST_RUN_LOCK:
        return _read_last_run().get(key, default)


def _last_run_set(key: str, value: Any) -> None:
    with _LAST_RUN_LOCK:
        data = _read_last_run()
        data[key] = value
        _write_last_run(data)


def _apply_last_run_deltas(deltas: Optional[dict[str, int]]) -> None:
    if not deltas:
        return
    with _LAST_RUN_LOCK:
        data = _read_last_run()
        for key, delta in deltas.items():
            if not delta:
                continue
            try:
                cur = int(data.get(key, 0) or 0)
            except (TypeError, ValueError):
                cur = 0
            data[key] = cur + int(delta)
        _write_last_run(data)


def _ensure_install_id() -> str:
    with _LAST_RUN_LOCK:
        data = _read_last_run()
        install_id = data.get(_INSTALL_ID_KEY, "")
        if isinstance(install_id, str) and install_id.strip():
            return install_id.strip()
        install_id = str(uuid.uuid4())
        data[_INSTALL_ID_KEY] = install_id
        _write_last_run(data)
        return install_id


# --- secrets ----------------------------------------------------------------------


def _iter_secrets_paths() -> list[Path]:
    """Locations for ``fido.secrets.json`` (first existing wins)."""
    paths: list[Path] = []
    try:
        import sys

        if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
            paths.append(Path(sys._MEIPASS) / _SECRETS_BASENAME)
    except Exception:
        pass
    try:
        from .paths import application_dir

        paths.append(application_dir() / _SECRETS_BASENAME)
    except Exception:
        pass
    # Dev: CheckMate repo root and sibling FIDO checkout
    try:
        repo_root = Path(__file__).resolve().parents[1]
        paths.append(repo_root / _SECRETS_BASENAME)
        paths.append(repo_root.parent / "FIDO" / _SECRETS_BASENAME)
    except Exception:
        pass
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _load_secrets_dict() -> dict[str, Any]:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE
    _SECRETS_CACHE = {}
    for path in _iter_secrets_paths():
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(raw, dict):
            _SECRETS_CACHE = raw
            break
    return _SECRETS_CACHE


def _string_from_mapping(block: Any, *keys: str) -> str:
    if not isinstance(block, dict):
        return ""
    for key in keys:
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _resolve_openpanel_credentials() -> tuple[str, str, Optional[str]]:
    block = _load_secrets_dict().get("openpanel")
    f_id = _string_from_mapping(block, "CLIENT_ID", "client_id")
    f_secret = _string_from_mapping(block, "CLIENT_SECRET", "client_secret")
    f_url = _string_from_mapping(block, "API_URL", "api_url")
    e_id = os.environ.get("FIDO_OPENPANEL_CLIENT_ID", "").strip()
    e_secret = os.environ.get("FIDO_OPENPANEL_CLIENT_SECRET", "").strip()
    e_url = os.environ.get("FIDO_OPENPANEL_API_URL", "").strip()
    client_id = e_id or f_id
    client_secret = e_secret or f_secret
    api_url = (e_url or f_url or "").strip() or None
    return client_id, client_secret, api_url


def _resolve_posthog_credentials() -> tuple[str, str]:
    block = _load_secrets_dict().get("posthog")
    f_token = _string_from_mapping(
        block, "PROJECT_TOKEN", "project_token", "API_KEY", "api_key"
    )
    f_host = _string_from_mapping(block, "API_HOST", "api_host", "HOST", "host")
    e_token = os.environ.get("FIDO_POSTHOG_PROJECT_TOKEN", "").strip()
    e_host = os.environ.get("FIDO_POSTHOG_API_HOST", "").strip()
    return (e_token or f_token), ((e_host or f_host or "").strip())


# --- HTTP senders -----------------------------------------------------------------


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> None:
    try:
        import requests

        requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception:
        logger.debug("Telemetry POST failed: %s", url, exc_info=True)


def _send_openpanel(
    *,
    client_id: str,
    client_secret: str,
    api_url: Optional[str],
    event_name: str,
    properties: dict[str, Any],
    install_id: str,
) -> None:
    base = (api_url or _DEFAULT_OPENPANEL_URL).rstrip("/")
    ua = f"CheckMate/{_checkmate_version()} ({_APP_KIND}; {platform.system()})"
    headers = {
        "Content-Type": "application/json",
        "openpanel-client-id": client_id,
        "openpanel-client-secret": client_secret,
        "openpanel-sdk-name": "checkmate",
        "openpanel-sdk-version": _checkmate_version() or "0",
        "User-Agent": ua,
    }
    # Identify once per process would be nicer; send identify traits on first track
    # by always including profileId (OpenPanel accepts track with profileId).
    payload = {
        "type": "track",
        "payload": {
            "name": event_name,
            "profileId": install_id,
            "properties": {
                "app": _APP_KIND,
                "checkmate_version": _checkmate_version(),
                "platform": platform.platform(),
                "anonymous_install": True,
                **properties,
            },
        },
    }
    _post_json(f"{base}/track", headers, payload)


def _send_posthog(
    *,
    token: str,
    host: str,
    event_name: str,
    properties: dict[str, Any],
    install_id: str,
) -> None:
    base = (host or "https://eu.i.posthog.com").rstrip("/")
    props = {
        "app": _APP_KIND,
        "checkmate_version": _checkmate_version(),
        "platform": platform.platform(),
        "anonymous_install": True,
        **properties,
    }
    payload = {
        "api_key": token,
        "event": event_name,
        "distinct_id": install_id,
        "properties": props,
    }
    _post_json(f"{base}/i/v0/e/", {"Content-Type": "application/json"}, payload)


def _dispatch_event(
    event_name: str,
    properties: dict[str, Any],
) -> None:
    if not is_telemetry_consent_granted():
        return
    install_id = _ensure_install_id()
    op_id, op_secret, op_url = _resolve_openpanel_credentials()
    if op_id and op_secret:
        _send_openpanel(
            client_id=op_id,
            client_secret=op_secret,
            api_url=op_url,
            event_name=event_name,
            properties=properties,
            install_id=install_id,
        )
    ph_token, ph_host = _resolve_posthog_credentials()
    if ph_token:
        _send_posthog(
            token=ph_token,
            host=ph_host,
            event_name=event_name,
            properties=properties,
            install_id=install_id,
        )


def _fire_and_forget(event_name: str, properties: dict[str, Any]) -> None:
    threading.Thread(
        target=_dispatch_event,
        args=(event_name, properties),
        daemon=True,
        name=f"checkmate-telemetry-{event_name}",
    ).start()


class SharedActivityLogger:
    """Consent-gated sender used when FIDO package is not imported."""

    def log_activity(
        self,
        activity_type: str,
        activity_model: str = "",
        activity_count: Optional[int] = None,
        additional_data: Optional[dict[str, Any]] = None,
        *,
        last_run_deltas: Optional[dict[str, int]] = None,
    ) -> None:
        # Deltas are applied by ``log_activity`` wrapper; ignore here if passed.
        _apply_last_run_deltas(last_run_deltas)
        if not is_telemetry_consent_granted():
            return
        props: dict[str, Any] = {}
        if activity_model:
            props["activity_model"] = activity_model
        if activity_count is not None:
            props["activity_count"] = int(activity_count)
        if additional_data:
            props.update(additional_data)
        _fire_and_forget(activity_type, props)

    def log_custom_event(self, event_name: str, properties: dict[str, Any]) -> None:
        if not is_telemetry_consent_granted():
            return
        _fire_and_forget(event_name, dict(properties or {}))

    def log_error(
        self,
        error_message: str,
        exception: Optional[BaseException] = None,
        additional_data: Optional[dict[str, Any]] = None,
    ) -> None:
        if not is_telemetry_consent_granted():
            return
        props: dict[str, Any] = {"error_message": error_message}
        if exception is not None:
            props["exception_type"] = type(exception).__name__
            props["exception_details"] = str(exception)
        if additional_data:
            props.update(additional_data)
        _fire_and_forget("error", props)


class _NoopLogger:
    def log_activity(
        self,
        activity_type: str,
        activity_model: str = "",
        activity_count: Optional[int] = None,
        additional_data: Optional[dict[str, Any]] = None,
        *,
        last_run_deltas: Optional[dict[str, int]] = None,
    ) -> None:
        _apply_last_run_deltas(last_run_deltas)

    def log_custom_event(self, event_name: str, properties: dict[str, Any]) -> None:
        return None

    def log_error(
        self,
        error_message: str,
        exception: Optional[BaseException] = None,
        additional_data: Optional[dict[str, Any]] = None,
    ) -> None:
        return None


def create_activity_logger():
    """Build a sender when consent + credentials exist; otherwise a no-op."""
    if not is_telemetry_consent_granted():
        return _NoopLogger()
    op_id, op_secret, _ = _resolve_openpanel_credentials()
    ph_token, _ = _resolve_posthog_credentials()
    if (op_id and op_secret) or ph_token:
        return SharedActivityLogger()
    return _NoopLogger()


def _get_logger():
    global _PROCESS_LOGGER
    if _PROCESS_LOGGER is not None:
        return _PROCESS_LOGGER
    try:
        import wx

        app = wx.GetApp()
        if app is not None:
            al = getattr(app, "activity_logger", None)
            if al is not None:
                return al
    except Exception:
        pass
    return None


def log_activity(
    activity_type: str,
    activity_model: str = "",
    activity_count: Optional[int] = None,
    additional_data: Optional[dict[str, Any]] = None,
    *,
    last_run_deltas: Optional[dict[str, int]] = None,
) -> None:
    """Record an event (local counters always; cloud only with consent + creds)."""
    try:
        _apply_last_run_deltas(last_run_deltas)
        if not is_telemetry_consent_granted():
            return
        props = dict(additional_data or {})
        props.setdefault("app", _APP_KIND)
        props.setdefault("checkmate_version", _checkmate_version())
        al = _get_logger()
        if al is not None:
            # Avoid double-counting last_run deltas.
            al.log_activity(activity_type, activity_model, activity_count, props)
        else:
            # No app logger yet (tests / early call): send directly.
            send_props = dict(props)
            if activity_model:
                send_props["activity_model"] = activity_model
            if activity_count is not None:
                send_props["activity_count"] = int(activity_count)
            _fire_and_forget(activity_type, send_props)
    except Exception:
        logger.debug("Telemetry log failed for %s", activity_type, exc_info=True)


def log_app_start() -> None:
    """Emit ``app_start`` / ``app_upgrade`` / ``first_install_launch`` like FIDO."""
    try:
        current = _checkmate_version()
        previous = (_last_run_get(_LAST_STARTED_VERSION_KEY, "") or "").strip()
        try:
            pre_total = int(_last_run_get(_APP_START_TOTAL_KEY, 0) or 0)
        except (TypeError, ValueError):
            pre_total = 0

        props: dict[str, Any] = {
            "platform": platform.system(),
            "checkmate_version": current,
        }

        if pre_total == 0 and not previous:
            log_activity("first_install_launch", "", additional_data=dict(props))

        log_activity(
            "app_start",
            "",
            additional_data=dict(props),
            last_run_deltas={_APP_START_TOTAL_KEY: 1},
        )

        if previous and current and previous != current:
            upgrade_props = dict(props)
            upgrade_props["previous_checkmate_version"] = previous
            log_activity("app_upgrade", "", additional_data=upgrade_props)

        if current:
            _last_run_set(_LAST_STARTED_VERSION_KEY, current)
    except Exception:
        logger.debug("log_app_start failed", exc_info=True)


def init_app_telemetry(app: Any) -> None:
    """Attach ``activity_logger`` on the wx App and record app start."""
    global _PROCESS_LOGGER
    try:
        _PROCESS_LOGGER = create_activity_logger()
        app.activity_logger = _PROCESS_LOGGER
    except Exception:
        logger.debug("Telemetry init failed", exc_info=True)
        _PROCESS_LOGGER = _NoopLogger()
        app.activity_logger = _PROCESS_LOGGER
    try:
        log_app_start()
    except Exception:
        logger.debug("app_start telemetry failed", exc_info=True)


def _ai_model() -> str:
    try:
        from .fido_settings import selected_model_service_string

        return selected_model_service_string() or ""
    except Exception:
        return ""


def log_check(result: Any) -> None:
    """Record a completed checker run (no paths or document content)."""
    try:
        verdict = getattr(getattr(result, "verdict", None), "value", "") or ""
        tool = (getattr(result, "tool_name", None) or "").strip()
        issues = getattr(result, "issues", None) or []
        log_activity(
            "checkmate_check",
            "",
            activity_count=len(issues) or None,
            additional_data={
                "verdict": verdict,
                "tool_name": tool,
                "fatals": int(getattr(result, "fatals", 0) or 0),
                "errors": int(getattr(result, "errors", 0) or 0),
                "warnings": int(getattr(result, "warnings", 0) or 0),
                "issue_count": len(issues),
            },
            last_run_deltas={"checkmate_check_total": 1},
        )
    except Exception:
        logger.debug("log_check failed", exc_info=True)


def log_ai_explain(*, followup: bool = False) -> None:
    event = "checkmate_ai_followup" if followup else "checkmate_ai_explain"
    delta_key = (
        "checkmate_ai_followup_total" if followup else "checkmate_ai_explain_total"
    )
    log_activity(
        event,
        _ai_model(),
        last_run_deltas={delta_key: 1},
    )


def log_ai_fix(*, applied: bool = False) -> None:
    if applied:
        log_activity(
            "checkmate_ai_fix_apply",
            _ai_model(),
            last_run_deltas={"checkmate_ai_fix_apply_total": 1},
        )
    else:
        log_activity(
            "checkmate_ai_fix",
            _ai_model(),
            last_run_deltas={"checkmate_ai_fix_total": 1},
        )


def log_ai_overview() -> None:
    log_activity(
        "checkmate_ai_overview",
        _ai_model(),
        last_run_deltas={"checkmate_ai_overview_total": 1},
    )
