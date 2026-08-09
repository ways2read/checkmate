"""On-demand offline DAISY Accessible Publishing Knowledge Base."""

from __future__ import annotations

from .fetch import fetch_article
from .store import (
    KB_HOME_URL,
    KbArticleRef,
    content_hash,
    en_relative_path_from_url,
    is_kb_url,
    load_manifest,
    mapped_article_paths,
    resolve_local_article,
    save_manifest,
)
from .update import update_knowledge_base

__all__ = [
    "KB_HOME_URL",
    "KbArticleRef",
    "content_hash",
    "en_relative_path_from_url",
    "fetch_article",
    "is_kb_url",
    "load_manifest",
    "mapped_article_paths",
    "resolve_local_article",
    "save_manifest",
    "update_knowledge_base",
]
