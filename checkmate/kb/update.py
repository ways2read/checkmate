"""User-triggered Knowledge Base update from live kb.daisy.org + GitHub metadata."""

from __future__ import annotations

from typing import Callable

import requests

from ..i18n import _
from .fetch import fetch_article, fetch_home_assets
from .store import (
    load_manifest,
    mapped_article_paths,
    prune_stale_translations,
    save_manifest,
)

ProgressCallback = Callable[[str], None]

GITHUB_COMMITS_API = (
    "https://api.github.com/repos/daisy/kb/commits"
    "?path=publishing&per_page=1"
)


def fetch_kb_repo_version(
    session: requests.Session | None = None,
) -> tuple[str, str]:
    """
    Return (commit_sha, commit_date_iso) for the latest change under publishing/.
    """
    own = session is None
    sess = session or requests.Session()
    try:
        resp = sess.get(
            GITHUB_COMMITS_API,
            timeout=30,
            headers={
                "User-Agent": "CheckMate/KB-offline (+https://daisy.org/)",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return "", ""
        item = data[0]
        sha = str(item.get("sha") or "")
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        date = str(author.get("date") or "")
        return sha, date
    finally:
        if own:
            sess.close()


def update_knowledge_base(
    *,
    paths: list[str] | None = None,
    also_ja: bool = True,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Refresh mapped (or given) seed articles from the live site and stamp version.

    Other articles are fetched on demand when opened in the viewer. This update
    only refreshes the Ace/EPUBCheck-mapped set plus shared site CSS/JS.

    Returns a summary dict: ``{ok, failed, commit_sha, commit_date, pruned, total}``.
    """
    targets = list(paths) if paths is not None else mapped_article_paths()
    if progress:
        progress(_("Preparing Knowledge Base update…"))

    sha, date = "", ""
    try:
        if progress:
            progress(_("Checking Knowledge Base version…"))
        sha, date = fetch_kb_repo_version()
    except requests.RequestException:
        sha, date = "", ""

    try:
        fetch_home_assets(progress=progress)
    except Exception:
        pass

    ok = 0
    failed: list[str] = []
    total = len(targets)
    for i, en_rel in enumerate(targets, start=1):
        if progress:
            progress(
                _("Downloading KB article {n} of {total}…", n=i, total=total)
            )
        try:
            fetch_article(en_rel, also_ja=also_ja, progress=None)
            ok += 1
        except Exception:
            failed.append(en_rel)

    manifest = load_manifest()
    if sha:
        manifest["commit_sha"] = sha
    if date:
        manifest["commit_date"] = date
    pruned = prune_stale_translations(manifest)
    save_manifest(manifest)

    if progress:
        if failed:
            progress(
                _("Knowledge Base update finished with {n} errors.", n=len(failed))
            )
        else:
            progress(_("Knowledge Base updated."))

    return {
        "ok": ok,
        "failed": failed,
        "commit_sha": sha,
        "commit_date": date,
        "pruned": pruned,
        "total": total,
    }
