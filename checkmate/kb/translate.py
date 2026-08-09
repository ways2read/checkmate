"""Translate-once cache for KB articles without an official locale."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..ai.litellm_client import ensure_credentials_ready, litellm_available
from ..ai.session import ExplainSession, ProviderError
from ..i18n import language_display_name
from .fetch import ARTICLE_OFFLINE_CSS, extract_article_fragment
from .store import (
    content_hash,
    en_file_path,
    load_manifest,
    save_manifest,
    translation_file_path,
    translation_meta_key,
)

logger = logging.getLogger(__name__)


@dataclass
class TranslationResult:
    path: Path | None = None
    error_key: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def _wrap_translated_page(*, title: str, lang: str, body_html: str, en_rel: str) -> str:
    from ..i18n import _

    note = _(
        "CheckMate translation of DAISY KB article {path}. "
        "Switch to Original English in the viewer for the authoritative text.",
        path=en_rel,
    )
    # Escape note lightly for HTML text context.
    note_html = (
        note.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""<!DOCTYPE html>
<html lang="{lang}" data-cm-kb="article">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
{ARTICLE_OFFLINE_CSS}
</style>
</head>
<body>
<p class="cm-kb-note">{note_html}</p>
<div id="cm-kb-body">
{body_html}
</div>
</body>
</html>
"""


def ensure_translation(
    en_rel: str,
    lang: str,
    *,
    force: bool = False,
) -> TranslationResult:
    """
    Ensure a translated HTML file exists for ``lang``.

    Official Japanese is handled by the mirror, not here.
    """
    code = (lang or "").strip().lower()
    if not code or code == "en" or code == "ja":
        return TranslationResult()
    base = code.split("-", 1)[0]

    en_path = en_file_path(en_rel)
    if not en_path.is_file():
        logger.warning("KB translation skipped; missing English file %s", en_rel)
        return TranslationResult(error_key="no_source")
    english = en_path.read_text(encoding="utf-8", errors="replace")
    en_hash = content_hash(english)

    dest = translation_file_path(base, en_rel)
    manifest = load_manifest()
    key = translation_meta_key(base, en_rel)
    meta = (manifest.get("translations") or {}).get(key)
    if (
        not force
        and dest.is_file()
        and isinstance(meta, dict)
        and str(meta.get("source_en_hash") or "") == en_hash
    ):
        return TranslationResult(path=dest)

    if not litellm_available():
        logger.error("KB translation requested but litellm is not installed")
        return TranslationResult(error_key="no_litellm")

    ok, err = ensure_credentials_ready()
    if not ok:
        logger.warning("KB translation credentials not ready: %s", err)
        return TranslationResult(error_key=err or "no_key")

    try:
        session = ExplainSession.create()
    except RuntimeError as e:
        return TranslationResult(error_key=str(e) or "no_key")

    conn_ok, conn_err, conn_detail = session.check_connection()
    if not conn_ok:
        return TranslationResult(
            error_key=conn_err or "network",
            detail=conn_detail or "",
        )

    display = language_display_name(base)
    fragment = extract_article_fragment(english)
    # Cap size to keep the completion tractable.
    if len(fragment) > 24000:
        fragment = fragment[:24000]

    system = (
        "You translate DAISY Accessible Publishing Knowledge Base HTML into "
        f"{display}. Preserve all HTML tags, attributes, and code samples exactly. "
        "Translate only human-readable text (headings, paragraphs, list items, "
        "figcaptions, summary text). Do not translate content inside <code>, <pre>, "
        "or attribute values. Return ONLY the translated HTML fragment — no markdown "
        "fences, no commentary."
    )
    user = (
        f"Translate the following HTML fragment into {display}.\n\n{fragment}"
    )
    try:
        translated = session.ask(system=system, user=user, max_tokens=8192)
    except ProviderError as e:
        logger.warning(
            "KB translation provider error key=%s detail=%s",
            e.error_key,
            e.detail,
        )
        return TranslationResult(error_key=e.error_key, detail=e.detail or "")
    except RuntimeError as e:
        return TranslationResult(error_key=str(e) or "no_key")
    except Exception as e:
        logger.exception("KB translation failed")
        return TranslationResult(error_key="provider_error", detail=str(e))

    text = (translated or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:html)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text:
        logger.warning("KB translation empty response model=%s", session.model)
        return TranslationResult(error_key="empty_response")

    title_m = re.search(r"<h1\b[^>]*>(.*?)</h1>", text, re.I | re.S)
    if not title_m:
        title_m = re.search(r"<h2\b[^>]*>(.*?)</h2>", text, re.I | re.S)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else en_rel
    page = _wrap_translated_page(
        title=title or en_rel, lang=base, body_html=text, en_rel=en_rel
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page, encoding="utf-8")

    translations = dict(manifest.get("translations") or {})
    translations[key] = {
        "source_en_hash": en_hash,
        "path": str(dest),
        "lang": base,
        "en_rel": en_rel,
    }
    manifest["translations"] = translations
    save_manifest(manifest)
    return TranslationResult(path=dest)
