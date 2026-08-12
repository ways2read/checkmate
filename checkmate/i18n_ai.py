"""AI-assisted translation of CheckMate UI string catalogs."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .ai.litellm_client import ensure_credentials_ready, litellm_available
from .ai.session import ExplainSession, ProviderError
from .i18n import (
    CUSTOM_I18N_FORMAT,
    CUSTOM_I18N_VERSION,
    TEXT_DIRECTION_LTR,
    _,
    bootstrap_msgids,
    install_custom_catalog,
    msgid_hash,
    read_catalog,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 90

# Preset languages offered in Add language… (code, native, English, direction).
UI_LANGUAGE_PRESETS: list[tuple[str, str, str, str]] = [
    ("it", "Italiano", "Italian", "ltr"),
    ("pl", "Polski", "Polish", "ltr"),
    ("uk", "Українська", "Ukrainian", "ltr"),
    ("cs", "Čeština", "Czech", "ltr"),
    ("tr", "Türkçe", "Turkish", "ltr"),
    ("zh-hans", "简体中文", "Chinese (Simplified)", "ltr"),
    ("de", "Deutsch", "German", "ltr"),
    ("pt", "Português", "Portuguese", "ltr"),
    ("he", "עברית", "Hebrew", "rtl"),
]


@dataclass
class UiTranslationResult:
    code: str = ""
    error_key: str | None = None
    detail: str = ""
    translated: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.error_key and bool(self.code)


ProgressFn = Callable[[str], None]


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_batch_json(text: str) -> dict[str, str]:
    raw = _strip_fences(text)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not_object")
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k:
            out[k] = v
    return out


def _keys_needing_translation(
    existing: dict[str, str],
    *,
    force: bool,
) -> list[str]:
    all_keys = bootstrap_msgids()
    if force:
        return all_keys
    return [k for k in all_keys if k not in existing]


def ensure_ui_translation(
    *,
    code: str,
    native_name: str,
    display_name: str,
    direction: str = TEXT_DIRECTION_LTR,
    force: bool = False,
    progress: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> UiTranslationResult:
    """
    Translate missing (or all) UI msgids into ``code`` and install the catalog.
    """

    def report(msg: str) -> None:
        if progress:
            progress(msg)

    if cancel_event and cancel_event.is_set():
        return UiTranslationResult(error_key="cancelled")

    if not litellm_available():
        return UiTranslationResult(error_key="no_litellm")

    ok, err = ensure_credentials_ready()
    if not ok:
        return UiTranslationResult(error_key=err or "no_key")

    try:
        session = ExplainSession.create()
    except RuntimeError as e:
        return UiTranslationResult(error_key=str(e) or "no_key")

    report(_("Checking AI connection…"))
    conn_ok, conn_err, conn_detail = session.check_connection(
        cancel_event=cancel_event
    )
    if cancel_event and cancel_event.is_set():
        return UiTranslationResult(error_key="cancelled")
    if not conn_ok:
        return UiTranslationResult(
            error_key=conn_err or "network",
            detail=conn_detail or "",
        )

    existing_catalog = read_catalog(code)
    existing_strings: dict[str, str] = {}
    if existing_catalog and not force:
        existing_strings = dict(existing_catalog.get("strings") or {})
        native_name = str(existing_catalog.get("native_name") or native_name)
        display_name = str(existing_catalog.get("display_name") or display_name)
        direction = str(existing_catalog.get("direction") or direction)
    elif existing_catalog and force:
        native_name = str(existing_catalog.get("native_name") or native_name)
        display_name = str(existing_catalog.get("display_name") or display_name)
        direction = str(existing_catalog.get("direction") or direction)

    todo = _keys_needing_translation(existing_strings, force=force)
    skipped = len(bootstrap_msgids()) - len(todo)
    if not todo:
        catalog = {
            "format": CUSTOM_I18N_FORMAT,
            "version": CUSTOM_I18N_VERSION,
            "code": code,
            "native_name": native_name,
            "display_name": display_name,
            "direction": direction,
            "source_msgid_hash": msgid_hash(),
            "strings": existing_strings,
        }
        try:
            install_custom_catalog(catalog, overwrite=True)
        except ValueError as e:
            return UiTranslationResult(error_key=str(e) or "install_failed")
        return UiTranslationResult(code=code, translated=0, skipped=skipped)

    merged = dict(existing_strings)
    translated_count = 0
    batches = [
        todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)
    ]
    total_batches = len(batches)

    system = (
        "You translate CheckMate desktop UI strings into "
        f"{display_name}. Return ONLY a JSON object mapping each English "
        "msgid key to its translation (same keys). Preserve accelerator "
        "ampersands (&), tab escapes (\\t), and curly brace placeholders "
        "like {n}, {path}, {detail} exactly. Do not add commentary or "
        "markdown fences."
    )

    for idx, batch in enumerate(batches, start=1):
        if cancel_event and cancel_event.is_set():
            return UiTranslationResult(error_key="cancelled")
        report(
            _("Translating UI strings ({current}/{total})…").format(
                current=idx, total=total_batches
            )
        )
        payload = {k: k for k in batch}
        user = (
            f"Translate these CheckMate UI strings into {display_name}. "
            "Keys must be preserved exactly.\n\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            raw = session.ask(system=system, user=user, max_tokens=8192)
        except ProviderError as e:
            logger.warning(
                "UI translation provider error key=%s detail=%s",
                e.error_key,
                e.detail,
            )
            return UiTranslationResult(
                error_key=e.error_key, detail=e.detail or ""
            )
        except RuntimeError as e:
            return UiTranslationResult(error_key=str(e) or "no_key")
        except Exception as e:
            logger.exception("UI translation failed")
            return UiTranslationResult(error_key="provider_error", detail=str(e))

        try:
            part = _parse_batch_json(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("UI translation bad JSON: %s", e)
            return UiTranslationResult(
                error_key="empty_response",
                detail=str(e),
            )

        for k in batch:
            val = part.get(k)
            if isinstance(val, str) and val.strip():
                merged[k] = val
                translated_count += 1
            elif k not in merged:
                # Keep English so the catalog stays usable.
                merged[k] = k

        # Incremental save so a later cancel still keeps progress.
        interim = {
            "format": CUSTOM_I18N_FORMAT,
            "version": CUSTOM_I18N_VERSION,
            "code": code,
            "native_name": native_name,
            "display_name": display_name,
            "direction": direction,
            "source_msgid_hash": "",
            "strings": merged,
        }
        try:
            install_custom_catalog(interim, overwrite=True)
        except ValueError as e:
            return UiTranslationResult(error_key=str(e) or "install_failed")

    final = {
        "format": CUSTOM_I18N_FORMAT,
        "version": CUSTOM_I18N_VERSION,
        "code": code,
        "native_name": native_name,
        "display_name": display_name,
        "direction": direction,
        "source_msgid_hash": msgid_hash(),
        "strings": merged,
    }
    try:
        install_custom_catalog(final, overwrite=True)
    except ValueError as e:
        return UiTranslationResult(error_key=str(e) or "install_failed")

    return UiTranslationResult(
        code=code,
        translated=translated_count,
        skipped=skipped,
    )
