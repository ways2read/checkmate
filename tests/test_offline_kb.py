"""Tests for on-demand offline DAISY Knowledge Base store/resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from checkmate.kb import store
from checkmate.kb.fetch import (
    ARTICLE_OFFLINE_CSS,
    extract_article_fragment,
    is_slim_article,
    refresh_article_offline_styles,
    slim_article_document,
)
from checkmate.kb.store import (
    content_hash,
    en_relative_path_from_url,
    is_kb_url,
    load_manifest,
    mapped_article_paths,
    prune_stale_translations,
    resolve_local_article,
    save_manifest,
    translation_meta_key,
)
from checkmate.kb.translate import _wrap_translated_page


class KbUrlTests(unittest.TestCase):
    def test_is_kb_url(self) -> None:
        self.assertTrue(
            is_kb_url("https://kb.daisy.org/publishing/docs/html/lang.html")
        )
        self.assertTrue(
            is_kb_url("http://kb.daisy.org/publishing/docs/html/lang.html")
        )
        self.assertFalse(is_kb_url("https://example.com/publishing/docs/x.html"))
        self.assertFalse(is_kb_url("https://www.w3.org/publishing/epubcheck/"))

    def test_en_relative_from_english_url(self) -> None:
        self.assertEqual(
            en_relative_path_from_url(
                "https://kb.daisy.org/publishing/docs/html/lang.html"
            ),
            "docs/html/lang.html",
        )

    def test_en_relative_from_japanese_url(self) -> None:
        self.assertEqual(
            en_relative_path_from_url(
                "https://kb.daisy.org/publishing/ja/html/lang.html"
            ),
            "docs/html/lang.html",
        )

    def test_en_relative_home(self) -> None:
        self.assertEqual(
            en_relative_path_from_url("https://kb.daisy.org/publishing/"),
            "docs/index.html",
        )


class KbStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._kb_dir_patch = mock.patch.object(store, "kb_dir", return_value=self.root)
        # store.kb_dir is imported from paths into functions via kb_dir() calls —
        # patch checkmate.kb.store.kb_dir and checkmate.paths.kb_dir.
        self._patches = [
            mock.patch("checkmate.kb.store.kb_dir", return_value=self.root),
            mock.patch("checkmate.paths.kb_dir", return_value=self.root),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_mapped_paths_include_known_articles(self) -> None:
        paths = mapped_article_paths()
        self.assertIn("docs/html/images.html", paths)
        self.assertIn("docs/html/lang.html", paths)
        self.assertIn("docs/index.html", paths)
        self.assertEqual(len(paths), len(set(paths)))

    def test_resolve_prefers_translation_then_english(self) -> None:
        en_rel = "docs/html/lang.html"
        en_path = self.root / "en" / en_rel
        en_path.parent.mkdir(parents=True)
        en_html = "<html><body><h1>Language</h1></body></html>"
        en_path.write_text(en_html, encoding="utf-8")
        en_hash = content_hash(en_html)

        tr_path = self.root / "translations" / "fr" / en_rel
        tr_path.parent.mkdir(parents=True)
        tr_path.write_text("<html><body><h1>Langue</h1></body></html>", encoding="utf-8")

        manifest = load_manifest()
        manifest["articles"] = {en_rel: {"en_hash": en_hash}}
        manifest["translations"] = {
            translation_meta_key("fr", en_rel): {
                "source_en_hash": en_hash,
                "path": str(tr_path),
            }
        }
        save_manifest(manifest)

        ref = resolve_local_article(en_rel, ui_lang="fr")
        assert ref is not None
        self.assertEqual(ref.preferred_kind, "translation")
        self.assertEqual(ref.preferred_path, tr_path)
        self.assertEqual(ref.en_path, en_path)

        en_ref = resolve_local_article(en_rel, ui_lang="fr", prefer_english=True)
        assert en_ref is not None
        self.assertEqual(en_ref.preferred_kind, "en")
        self.assertEqual(en_ref.preferred_path, en_path)

    def test_stale_translation_ignored_and_pruned(self) -> None:
        en_rel = "docs/html/lang.html"
        en_path = self.root / "en" / en_rel
        en_path.parent.mkdir(parents=True)
        en_path.write_text("<html>v2</html>", encoding="utf-8")
        new_hash = content_hash("<html>v2</html>")

        tr_path = self.root / "translations" / "de" / en_rel
        tr_path.parent.mkdir(parents=True)
        tr_path.write_text("<html>alt</html>", encoding="utf-8")

        manifest = {
            "commit_sha": "",
            "commit_date": "",
            "updated_at": "",
            "articles": {en_rel: {"en_hash": new_hash}},
            "translations": {
                translation_meta_key("de", en_rel): {
                    "source_en_hash": "oldhash",
                    "path": str(tr_path),
                }
            },
        }
        save_manifest(manifest)

        ref = resolve_local_article(en_rel, ui_lang="de")
        assert ref is not None
        self.assertEqual(ref.preferred_kind, "en")
        self.assertIsNone(ref.translation_path)

        m = load_manifest()
        removed = prune_stale_translations(m)
        self.assertEqual(removed, 1)
        save_manifest(m)
        self.assertFalse(tr_path.is_file())

    def test_ja_preferred_for_japanese_ui(self) -> None:
        en_rel = "docs/html/lang.html"
        en_path = self.root / "en" / en_rel
        en_path.parent.mkdir(parents=True)
        en_path.write_text("<html>en</html>", encoding="utf-8")
        ja_path = self.root / "ja" / "html" / "lang.html"
        ja_path.parent.mkdir(parents=True)
        ja_path.write_text("<html>ja</html>", encoding="utf-8")
        save_manifest(
            {
                "articles": {en_rel: {"en_hash": content_hash("<html>en</html>")}},
                "translations": {},
            }
        )
        ref = resolve_local_article(en_rel, ui_lang="ja")
        assert ref is not None
        self.assertEqual(ref.preferred_kind, "ja")
        self.assertEqual(ref.preferred_path, ja_path)


class KbManifestRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = mock.patch("checkmate.kb.store.kb_dir", return_value=self.root)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_save_load(self) -> None:
        save_manifest(
            {
                "commit_sha": "abc",
                "commit_date": "2026-06-01T20:27:22Z",
                "articles": {"docs/html/lang.html": {"en_hash": "x"}},
                "translations": {},
            }
        )
        m = load_manifest()
        self.assertEqual(m["commit_sha"], "abc")
        self.assertTrue(m["updated_at"])
        raw = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["commit_date"], "2026-06-01T20:27:22Z")


_SAMPLE_KB_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><title>Language - Accessible Publishing Knowledge Base</title></head>
<body>
<header><h1>KB</h1><div class="gcse-searchbox-only"></div></header>
<main id="main" class="category">
<aside id="sponsor">Sponsor</aside>
<div id="col-wrapper">
<div id="nav-col"><nav id="mini-nav">TOC</nav></div>
<div id="body">
<div id="page-title"><h2>Language</h2></div>
<section id="summary"><h3>Summary</h3><p>Set the language.</p></section>
</div>
<div id="categories"><nav>Categories</nav></div>
</div>
</main>
<footer>Footer</footer>
</body>
</html>
"""


class KbArticleExtractTests(unittest.TestCase):
    def test_extracts_body_without_site_chrome(self) -> None:
        frag = extract_article_fragment(_SAMPLE_KB_PAGE)
        self.assertIn("Set the language.", frag)
        self.assertIn('id="summary"', frag)
        self.assertNotIn("Sponsor", frag)
        self.assertNotIn("mini-nav", frag)
        self.assertNotIn("Categories", frag)
        self.assertNotIn("<header>", frag)
        self.assertNotIn("<footer>", frag)

    def test_slim_document_is_marked_and_idempotent(self) -> None:
        slim = slim_article_document(_SAMPLE_KB_PAGE)
        self.assertTrue(is_slim_article(slim))
        self.assertIn("<title>Language</title>", slim)
        self.assertNotIn("<header>", slim)
        again = slim_article_document(slim)
        self.assertEqual(again, slim)
        self.assertEqual(
            extract_article_fragment(slim).strip(),
            extract_article_fragment(_SAMPLE_KB_PAGE).strip(),
        )

    def test_slim_and_translated_share_offline_css(self) -> None:
        slim = slim_article_document(_SAMPLE_KB_PAGE)
        translated = _wrap_translated_page(
            title="Language",
            lang="fr",
            body_html="<h2>Langue</h2><table><tr><td>x</td></tr></table>",
            en_rel="docs/html/lang.html",
        )
        for doc in (slim, translated, ARTICLE_OFFLINE_CSS):
            self.assertIn("table {", doc)
            self.assertIn("pre {", doc)
            self.assertIn(".category {", doc)

    def test_refresh_upgrades_thin_translation_css(self) -> None:
        thin = """<!DOCTYPE html>
<html lang="fr" data-cm-kb="article">
<head><title>x</title>
<style>
body { font-family: system-ui; }
.cm-kb-note { color: #444; }
</style>
</head>
<body><p class="cm-kb-note">note</p><div id="cm-kb-body"><table><tr><td>a</td></tr></table></div></body>
</html>
"""
        refreshed = refresh_article_offline_styles(thin)
        self.assertIn("table {", refreshed)
        self.assertIn("pre {", refreshed)
        self.assertIn("note", refreshed)
        self.assertIn("<table>", refreshed)
        # slim path also refreshes
        via_slim = slim_article_document(thin)
        self.assertIn("table {", via_slim)


if __name__ == "__main__":
    unittest.main()
