"""Local offline KB store under AppData/CheckMate/kb."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

from ..ai.ace_kb_map import ACE_RULE_KB_PATHS, KB_PUBLISHING_BASE, normalize_kb_url
from ..ai.epubcheck_kb_map import EPUBCHECK_ACC_KB_PATHS, EPUBCHECK_OTHER_KB_PATHS
from ..paths import kb_dir

KB_HOME_URL = normalize_kb_url(KB_PUBLISHING_BASE)
KB_HOST = "kb.daisy.org"
MANIFEST_NAME = "manifest.json"

# Official mirrored locales (French site section is a stub — not treated as official).
OFFICIAL_LOCALES = frozenset({"en", "ja"})

_KB_URL_RE = re.compile(
    r"^https?://kb\.daisy\.org/publishing/(?P<rest>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KbArticleRef:
    """Resolved local article variants for one English canonical path."""

    en_rel: str
    en_path: Path | None
    preferred_path: Path | None
    preferred_kind: str  # "en" | "ja" | "translation"
    preferred_lang: str
    translation_path: Path | None
    translation_lang: str | None
    online_url: str


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def is_kb_url(url: str) -> bool:
    u = normalize_kb_url(url or "")
    if not u:
        return False
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    return parsed.netloc.lower() == KB_HOST and "/publishing" in (parsed.path or "")


def mapped_article_paths() -> list[str]:
    """Unique English relative paths from Ace + EPUBCheck maps (docs/...)."""
    paths: set[str] = set()
    for mapping in (ACE_RULE_KB_PATHS, EPUBCHECK_ACC_KB_PATHS, EPUBCHECK_OTHER_KB_PATHS):
        for _title, rel in mapping.values():
            rel = (rel or "").strip().lstrip("/")
            if rel:
                paths.add(rel)
    # Always include the English home/index when present in maps' tree.
    paths.add("docs/index.html")
    return sorted(paths)


def en_relative_path_from_url(url: str) -> str | None:
    """
    Map a kb.daisy.org URL to the English relative path under publishing/
    (e.g. ``docs/html/lang.html``). Japanese URLs are normalized to English.
    """
    u = normalize_kb_url(url or "")
    m = _KB_URL_RE.match(u.split("#", 1)[0].split("?", 1)[0])
    if not m:
        return None
    rest = unquote(m.group("rest") or "").lstrip("/")
    if not rest:
        return "docs/index.html"
    if rest in {"", "index.html"}:
        return "docs/index.html"
    # Official JA lives at publishing/ja/... (no docs/ prefix).
    if rest == "ja" or rest.startswith("ja/"):
        tail = rest[3:] if rest.startswith("ja/") else ""
        if not tail or tail.endswith("/"):
            tail = (tail or "") + "index.html"
        return f"docs/{tail}" if not tail.startswith("docs/") else tail
    # French stub — treat as docs/ when under fr/
    if rest == "fr" or rest.startswith("fr/"):
        tail = rest[3:] if rest.startswith("fr/") else ""
        if not tail or tail.endswith("/"):
            tail = (tail or "") + "index.html"
        return f"docs/{tail}" if not tail.startswith("docs/") else tail
    if not rest.startswith("docs/"):
        # Bare publishing/docs redirect targets etc.
        if rest.endswith("/"):
            rest = rest + "index.html"
        return rest if rest.startswith("docs/") else f"docs/{rest}"
    if rest.endswith("/"):
        rest = rest + "index.html"
    return rest


def online_url_for_en_rel(en_rel: str, *, locale: str = "en") -> str:
    rel = en_rel.lstrip("/")
    if locale == "en":
        return normalize_kb_url(KB_PUBLISHING_BASE + rel)
    if locale == "ja":
        # docs/html/foo.html → ja/html/foo.html
        if rel.startswith("docs/"):
            ja_rel = "ja/" + rel[len("docs/") :]
        else:
            ja_rel = f"ja/{rel}"
        return normalize_kb_url(KB_PUBLISHING_BASE + ja_rel)
    return normalize_kb_url(KB_PUBLISHING_BASE + rel)


def ja_relative_from_en(en_rel: str) -> str:
    rel = en_rel.lstrip("/")
    if rel.startswith("docs/"):
        return "ja/" + rel[len("docs/") :]
    return f"ja/{rel}"


def empty_manifest() -> dict[str, Any]:
    return {
        "commit_sha": "",
        "commit_date": "",
        "updated_at": "",
        "articles": {},  # en_rel -> {en_hash, ja_hash?, assets: [...]}
        "translations": {},  # "{lang}:{en_rel}" -> {source_en_hash, path}
    }


def manifest_path() -> Path:
    return kb_dir() / MANIFEST_NAME


def load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.is_file():
        return empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return empty_manifest()
    if not isinstance(data, dict):
        return empty_manifest()
    base = empty_manifest()
    base.update(data)
    if not isinstance(base.get("articles"), dict):
        base["articles"] = {}
    if not isinstance(base.get("translations"), dict):
        base["translations"] = {}
    return base


def save_manifest(manifest: dict[str, Any]) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(manifest)
    payload["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def en_file_path(en_rel: str) -> Path:
    return kb_dir() / "en" / Path(en_rel.lstrip("/"))


def ja_file_path(en_rel: str) -> Path:
    ja_rel = ja_relative_from_en(en_rel)
    if ja_rel.startswith("ja/"):
        ja_rel = ja_rel[3:]
    return kb_dir() / "ja" / Path(ja_rel)


def translation_file_path(lang: str, en_rel: str) -> Path:
    safe_lang = (lang or "und").strip().lower() or "und"
    return kb_dir() / "translations" / safe_lang / Path(en_rel.lstrip("/"))


def translation_meta_key(lang: str, en_rel: str) -> str:
    return f"{(lang or '').strip().lower()}:{en_rel.lstrip('/')}"


def has_any_articles() -> bool:
    manifest = load_manifest()
    return bool(manifest.get("articles"))


def kb_version_label(manifest: dict[str, Any] | None = None) -> str:
    m = manifest if manifest is not None else load_manifest()
    date = (m.get("commit_date") or "").strip()
    if date:
        # Prefer YYYY-MM-DD from ISO timestamps.
        return date[:10]
    updated = (m.get("updated_at") or "").strip()
    return updated[:10] if updated else ""


def resolve_local_article(
    url_or_en_rel: str,
    *,
    ui_lang: str,
    prefer_english: bool = False,
) -> KbArticleRef | None:
    """
    Resolve local paths for a KB URL or English relative path.

    When ``prefer_english`` is True, preferred_path is the English file when present.
    """
    raw = (url_or_en_rel or "").strip()
    if not raw:
        return None
    if is_kb_url(raw) or raw.startswith(("http://", "https://")):
        en_rel = en_relative_path_from_url(raw)
    else:
        en_rel = raw.lstrip("/")
    if not en_rel:
        return None

    en_path = en_file_path(en_rel)
    en_ok = en_path.is_file()
    ja_path = ja_file_path(en_rel)
    ja_ok = ja_path.is_file()

    lang = (ui_lang or "en").strip().lower() or "en"
    # Normalize regional codes (pt-br → pt) for translation folder lookup.
    base_lang = lang.split("-", 1)[0]

    tr_path: Path | None = None
    tr_lang: str | None = None
    manifest = load_manifest()
    for candidate in (lang, base_lang):
        if candidate in ("en",):
            continue
        if candidate == "ja" and ja_ok:
            continue
        meta = manifest.get("translations", {}).get(
            translation_meta_key(candidate, en_rel)
        )
        path = translation_file_path(candidate, en_rel)
        if path.is_file():
            # Stale if English hash moved.
            en_hash = ""
            art = manifest.get("articles", {}).get(en_rel) or {}
            if isinstance(art, dict):
                en_hash = str(art.get("en_hash") or "")
            source_hash = ""
            if isinstance(meta, dict):
                source_hash = str(meta.get("source_en_hash") or "")
            if en_hash and source_hash and source_hash != en_hash:
                continue
            tr_path = path
            tr_lang = candidate
            break

    preferred_path: Path | None = None
    preferred_kind = "en"
    preferred_lang = "en"

    if prefer_english and en_ok:
        preferred_path = en_path
        preferred_kind = "en"
        preferred_lang = "en"
    elif base_lang == "ja" and ja_ok:
        preferred_path = ja_path
        preferred_kind = "ja"
        preferred_lang = "ja"
    elif tr_path is not None:
        preferred_path = tr_path
        preferred_kind = "translation"
        preferred_lang = tr_lang or base_lang
    elif en_ok:
        preferred_path = en_path
        preferred_kind = "en"
        preferred_lang = "en"
    elif ja_ok:
        preferred_path = ja_path
        preferred_kind = "ja"
        preferred_lang = "ja"

    return KbArticleRef(
        en_rel=en_rel,
        en_path=en_path if en_ok else None,
        preferred_path=preferred_path,
        preferred_kind=preferred_kind,
        preferred_lang=preferred_lang,
        translation_path=tr_path,
        translation_lang=tr_lang,
        online_url=online_url_for_en_rel(en_rel, locale="en"),
    )


def prune_stale_translations(manifest: dict[str, Any]) -> int:
    """Remove translation files/meta whose source_en_hash no longer matches."""
    removed = 0
    articles = manifest.get("articles") or {}
    translations = dict(manifest.get("translations") or {})
    for key, meta in list(translations.items()):
        if not isinstance(meta, dict):
            translations.pop(key, None)
            removed += 1
            continue
        lang, _, en_rel = key.partition(":")
        art = articles.get(en_rel) if isinstance(articles, dict) else None
        en_hash = ""
        if isinstance(art, dict):
            en_hash = str(art.get("en_hash") or "")
        source = str(meta.get("source_en_hash") or "")
        path_str = str(meta.get("path") or "")
        path = Path(path_str) if path_str else translation_file_path(lang, en_rel)
        if en_hash and source and source != en_hash:
            translations.pop(key, None)
            removed += 1
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
    manifest["translations"] = translations
    return removed
