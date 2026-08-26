"""HTML classification, crawl, Nu/axe JSON mapping, and alt-text export."""

from __future__ import annotations

import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError

from checkmate.axe_html import issues_from_axe_results, parse_axe_runner_output
from checkmate.html_check import merge_vnu_and_axe, run_html_check
from checkmate.html_crawl import (
    DEFAULT_CRAWL_CAP,
    LocalHtmlServer,
    crawl_html_pages,
    https_to_http_url,
    is_tls_handshake_failure,
    local_start_url,
    pages_for_html_check,
    prefer_working_page_url,
    should_follow_href,
)
from checkmate.models import CheckResult, Severity, Verdict
from checkmate.publication import (
    PublicationKind,
    classify_publication,
    classify_target,
    is_checkable_target,
    is_html_url,
)
from checkmate.report_export import report_title
from checkmate.vnu_check import (
    VNU_ALLOW_FORBIDDEN_HOSTS,
    document_arg_for_vnu,
    issues_from_vnu_json,
    severity_from_vnu_message,
    vnu_argv,
)
from checkmate import settings as settings_mod


class ClassifyHtmlTests(unittest.TestCase):
    def test_html_url(self) -> None:
        self.assertTrue(is_html_url("https://example.com/page"))
        self.assertTrue(is_html_url("http://127.0.0.1:8080/index.html"))
        self.assertFalse(is_html_url("ftp://example.com/a"))
        self.assertFalse(is_html_url(r"C:\docs\page.html"))
        self.assertEqual(
            classify_target("https://example.org/a.html"), PublicationKind.HTML
        )
        self.assertTrue(is_checkable_target("https://example.org/a.html"))

    def test_html_file_and_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "index.html"
            page.write_text("<html><body>Hi</body></html>", encoding="utf-8")
            nested = root / "sub"
            nested.mkdir()
            (nested / "other.htm").write_text("<p>x</p>", encoding="utf-8")
            self.assertEqual(classify_publication(page), PublicationKind.HTML)
            self.assertEqual(classify_publication(root), PublicationKind.HTML)
            self.assertEqual(classify_target(str(page)), PublicationKind.HTML)
            empty = root / "empty"
            empty.mkdir()
            self.assertEqual(classify_publication(empty), PublicationKind.UNSUPPORTED)

    def test_daisy_folder_not_stolen_as_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ncc.html").write_text("<html></html>", encoding="utf-8")
            self.assertEqual(classify_publication(root), PublicationKind.DAISY202)


class CrawlTests(unittest.TestCase):
    def test_skips_mailto_and_binaries(self) -> None:
        base = "http://example.com/a.html"
        self.assertIsNone(should_follow_href(base, "mailto:x@y.z"))
        self.assertIsNone(should_follow_href(base, "javascript:void(0)"))
        self.assertIsNone(should_follow_href(base, "photo.png"))
        self.assertIsNone(should_follow_href(base, "https://other.example/x"))
        self.assertEqual(
            should_follow_href(base, "b.html"),
            "http://example.com/b.html",
        )
        self.assertEqual(
            should_follow_href(base, "#frag"),
            None,
        )

    def test_crawl_cap_and_same_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "start.html").write_text(
                '<a href="a.html">A</a><a href="b.html">B</a>'
                '<a href="https://example.com/off">off</a>',
                encoding="utf-8",
            )
            (root / "a.html").write_text(
                '<a href="c.html">C</a><a href="start.html">back</a>',
                encoding="utf-8",
            )
            (root / "b.html").write_text("<p>b</p>", encoding="utf-8")
            (root / "c.html").write_text("<p>c</p>", encoding="utf-8")
            with LocalHtmlServer(root) as server:
                start = local_start_url(root / "start.html", server.origin)
                pages = crawl_html_pages(start, cap=3, local_root=root)
            self.assertEqual(len(pages), 3)
            self.assertTrue(pages[0].endswith("/start.html"))
            self.assertLessEqual(len(pages), DEFAULT_CRAWL_CAP)
            for url in pages:
                self.assertTrue(url.startswith(server.origin))

    def test_follow_links_off_is_start_page_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "start.html").write_text(
                '<a href="a.html">A</a>', encoding="utf-8"
            )
            (root / "a.html").write_text("<p>a</p>", encoding="utf-8")
            with LocalHtmlServer(root) as server:
                start = local_start_url(root / "start.html", server.origin)
                pages = pages_for_html_check(
                    start, follow_links=False, cap=25, local_root=root
                )
            self.assertEqual(len(pages), 1)
            self.assertTrue(pages[0].endswith("/start.html"))


class VnuMapperTests(unittest.TestCase):
    def test_severity_and_location(self) -> None:
        self.assertEqual(
            severity_from_vnu_message({"type": "error"}), Severity.ERROR
        )
        self.assertEqual(
            severity_from_vnu_message({"type": "info", "subtype": "warning"}),
            Severity.WARNING,
        )
        self.assertEqual(
            severity_from_vnu_message({"type": "info"}), Severity.INFO
        )
        data = {
            "messages": [
                {
                    "type": "error",
                    "lastLine": 4,
                    "lastColumn": 12,
                    "message": "Start tag seen without seeing a doctype first.",
                    "url": "http://127.0.0.1:9/x.html",
                    "extract": "<html>",
                },
                {
                    "type": "info",
                    "subtype": "warning",
                    "lastLine": 8,
                    "lastColumn": 1,
                    "message": "Consider adding a lang attribute.",
                    "url": "http://127.0.0.1:9/x.html",
                },
            ]
        }
        issues = issues_from_vnu_json(data)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].source, "Nu HTML Checker")
        self.assertEqual(issues[0].severity, Severity.ERROR)
        self.assertIn("4:12", issues[0].location)
        self.assertEqual(issues[1].severity, Severity.WARNING)


class VnuLocalhostTests(unittest.TestCase):
    def test_argv_allows_forbidden_hosts_before_jar(self) -> None:
        cmd = vnu_argv("java", Path("vnu.jar"), "http://127.0.0.1:9/x.html")
        self.assertEqual(cmd[1], VNU_ALLOW_FORBIDDEN_HOSTS)
        self.assertEqual(cmd[2], "-jar")
        self.assertEqual(cmd[-1], "http://127.0.0.1:9/x.html")

    def test_argv_keeps_extra_args_before_target(self) -> None:
        cmd = vnu_argv(
            "java", Path("vnu.jar"), "icon.svg", extra_args=["--svg"]
        )
        self.assertEqual(cmd[-2:], ["--svg", "icon.svg"])
        self.assertEqual(cmd[1], VNU_ALLOW_FORBIDDEN_HOSTS)

    def test_maps_loopback_url_to_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "index.html"
            page.write_text("<p>x</p>", encoding="utf-8")
            mapped = document_arg_for_vnu(
                "http://127.0.0.1:53757/index.html", root
            )
            self.assertEqual(Path(mapped).resolve(), page.resolve())

    def test_leaves_remote_url_unchanged(self) -> None:
        url = "https://example.com/a.html"
        self.assertEqual(document_arg_for_vnu(url), url)
        self.assertEqual(document_arg_for_vnu(url, None), url)

    def test_run_vnu_passes_allow_forbidden_hosts(self) -> None:
        from checkmate import vnu_check

        captured: list[list[str]] = []
        fake_java = mock.Mock()
        fake_java.path = "java"
        proc = mock.Mock()
        proc.stdout = '{"messages":[]}'
        proc.stderr = ""
        proc.returncode = 0

        def capturing(cmd, **_kwargs):
            captured.append(list(cmd))
            return proc

        with (
            mock.patch.object(vnu_check, "cached_java", return_value=fake_java),
            mock.patch.object(
                vnu_check, "ensure_vnu_jar", return_value=Path("vnu.jar")
            ),
            mock.patch.object(
                vnu_check, "vnu_version_text", return_value="26.8.15"
            ),
            mock.patch.object(vnu_check, "run_capturing", side_effect=capturing),
        ):
            result = vnu_check.run_vnu_on_urls(["http://127.0.0.1:9/x.html"])
        self.assertEqual(result.verdict, Verdict.PASSED)
        self.assertEqual(len(captured), 1)
        self.assertIn(VNU_ALLOW_FORBIDDEN_HOSTS, captured[0])
        self.assertLess(
            captured[0].index(VNU_ALLOW_FORBIDDEN_HOSTS),
            captured[0].index("-jar"),
        )

    def test_run_vnu_checks_local_file_not_loopback_url(self) -> None:
        from checkmate import vnu_check

        captured: list[list[str]] = []
        fake_java = mock.Mock()
        fake_java.path = "java"
        proc = mock.Mock()
        proc.stdout = '{"messages":[]}'
        proc.stderr = ""
        proc.returncode = 0

        def capturing(cmd, **_kwargs):
            captured.append(list(cmd))
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "index.html"
            page.write_text(
                "<!DOCTYPE html><html lang='en'><title>x</title></html>",
                encoding="utf-8",
            )
            with (
                mock.patch.object(vnu_check, "cached_java", return_value=fake_java),
                mock.patch.object(
                    vnu_check, "ensure_vnu_jar", return_value=Path("vnu.jar")
                ),
                mock.patch.object(
                    vnu_check, "vnu_version_text", return_value="26.8.15"
                ),
                mock.patch.object(
                    vnu_check, "run_capturing", side_effect=capturing
                ),
            ):
                vnu_check.run_vnu_on_urls(
                    ["http://127.0.0.1:9/index.html"],
                    local_root=root,
                )
        self.assertEqual(len(captured), 1)
        self.assertEqual(Path(captured[0][-1]).resolve(), page.resolve())
        self.assertFalse(captured[0][-1].startswith("http://"))


class AxeMapperTests(unittest.TestCase):
    def test_violations_and_incomplete(self) -> None:
        axe = {
            "violations": [
                {
                    "id": "image-alt",
                    "impact": "critical",
                    "help": "Images must have alternate text",
                    "helpUrl": "https://dequeuniversity.com/rules/axe/4.8/image-alt",
                    "tags": ["wcag2a", "wcag111", "cat.text-alternatives"],
                    "nodes": [
                        {
                            "html": '<img src="x.png">',
                            "target": ["img"],
                            "any": [{"message": "Element has no alt"}],
                        }
                    ],
                }
            ],
            "incomplete": [
                {
                    "id": "color-contrast",
                    "impact": "serious",
                    "help": "Elements must have sufficient color contrast",
                    "tags": ["wcag2aa"],
                    "nodes": [{"target": [".btn"]}],
                }
            ],
        }
        issues = issues_from_axe_results(axe, page_url="http://example.com/p")
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].code, "image-alt")
        self.assertEqual(issues[0].source, "axe")
        self.assertEqual(issues[0].severity, Severity.ERROR)
        self.assertEqual(issues[0].impact, "critical")
        self.assertIn("WCAG 2.0 A", issues[0].ruleset)
        self.assertIn("http://example.com/p", issues[0].location)
        self.assertEqual(
            issues[0].help_url,
            "https://dequeuniversity.com/rules/axe/4.8/image-alt",
        )
        self.assertEqual(issues[0].help_title, "Images must have alternate text")
        self.assertEqual(issues[1].severity, Severity.INFO)
        self.assertTrue(issues[1].message.startswith("Needs review:"))

    def test_runner_output_collects_images(self) -> None:
        data = {
            "pages": [
                {
                    "url": "http://example.com/p",
                    "axe": {"violations": [], "incomplete": []},
                    "images": [
                        {
                            "kind": "img",
                            "src": "http://example.com/logo.png",
                            "alt": "",
                            "altPresent": True,
                            "decorative": True,
                            "selector": "img",
                        }
                    ],
                }
            ]
        }
        issues, images = parse_axe_runner_output(data)
        self.assertEqual(issues, [])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["pageUrl"], "http://example.com/p")

    def test_ssl_protocol_error_explains_http_fallback(self) -> None:
        data = {
            "pages": [
                {
                    "url": "https://daisy.org.uk",
                    "error": "net::ERR_SSL_PROTOCOL_ERROR at https://daisy.org.uk",
                }
            ]
        }
        issues, _images = parse_axe_runner_output(data)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "axe-error")
        self.assertIn("TLS handshake", issues[0].message)
        self.assertIn("http://daisy.org.uk", issues[0].message)


class HttpsFallbackTests(unittest.TestCase):
    def test_https_to_http_url(self) -> None:
        self.assertEqual(
            https_to_http_url("https://daisy.org.uk/path?q=1"),
            "http://daisy.org.uk/path?q=1",
        )
        self.assertEqual(https_to_http_url("http://daisy.org.uk/"), "")

    def test_tls_handshake_failure_detects_ssl_error(self) -> None:
        wrapped = URLError(
            ssl.SSLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error")
        )
        self.assertTrue(is_tls_handshake_failure(wrapped))
        self.assertFalse(is_tls_handshake_failure(URLError("timed out")))

    def test_http_and_localhost_unchanged(self) -> None:
        url, note = prefer_working_page_url("http://daisy.org.uk/")
        self.assertEqual(url, "http://daisy.org.uk/")
        self.assertEqual(note, "")
        url, note = prefer_working_page_url("https://127.0.0.1:8443/x")
        self.assertEqual(url, "https://127.0.0.1:8443/x")
        self.assertEqual(note, "")

    def test_tls_failure_falls_back_when_http_works(self) -> None:
        ssl_err = URLError(
            ssl.SSLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error")
        )

        def probe(url: str, **_kwargs) -> None:
            if url.startswith("https://"):
                raise ssl_err

        with mock.patch("checkmate.html_crawl._probe_url", side_effect=probe):
            url, note = prefer_working_page_url("https://daisy.org.uk/")
        self.assertEqual(url, "http://daisy.org.uk/")
        self.assertIn("TLS handshake failed", note)
        self.assertIn("http://daisy.org.uk/", note)

    def test_keeps_https_when_http_also_fails(self) -> None:
        ssl_err = URLError(
            ssl.SSLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error")
        )

        def probe(_url: str, **_kwargs) -> None:
            raise ssl_err

        with mock.patch("checkmate.html_crawl._probe_url", side_effect=probe):
            url, note = prefer_working_page_url("https://example.com/")
        self.assertEqual(url, "https://example.com/")
        self.assertEqual(note, "")

    def test_non_tls_error_keeps_https(self) -> None:
        def probe(_url: str, **_kwargs) -> None:
            raise URLError("timed out")

        with mock.patch("checkmate.html_crawl._probe_url", side_effect=probe):
            url, note = prefer_working_page_url("https://example.com/")
        self.assertEqual(url, "https://example.com/")
        self.assertEqual(note, "")


class MergeAndReportTests(unittest.TestCase):
    def test_merge_sets_tool_name_and_pages(self) -> None:
        vnu = CheckResult(
            verdict=Verdict.FAILED,
            errors=1,
            issues=issues_from_vnu_json(
                {
                    "messages": [
                        {"type": "error", "message": "Bad markup", "lastLine": 1}
                    ]
                }
            ),
            tool_name="Nu HTML Checker",
        )
        from checkmate.models import Issue

        axe = CheckResult(
            verdict=Verdict.PASSED_WITH_WARNINGS,
            warnings=1,
            issues=[
                Issue(
                    severity=Severity.WARNING,
                    code="label",
                    message="Need a label",
                    source="axe",
                )
            ],
            tool_name="axe",
        )
        merged = merge_vnu_and_axe(
            vnu,
            axe,
            target="https://example.com/",
            pages=["https://example.com/", "https://example.com/a"],
            images=[],
        )
        self.assertEqual(merged.tool_name, "Nu HTML Checker + axe")
        self.assertEqual(merged.verdict, Verdict.FAILED)
        self.assertEqual(len(merged.source_counts), 2)
        self.assertEqual(len(merged.html_pages), 2)
        self.assertEqual(report_title(merged), "Nu HTML Checker + axe report")
        self.assertTrue(
            any(line.startswith("Web page:") for line in merged.report_meta_lines())
        )

    def test_merge_does_not_duplicate_vnu_version(self) -> None:
        vnu = CheckResult(
            verdict=Verdict.PASSED,
            tool_name="Nu HTML Checker",
            tool_version="26.8.15",
            extra_meta=[("Nu HTML Checker version", "26.8.15")],
        )
        merged = merge_vnu_and_axe(
            vnu,
            None,
            target="x.html",
            pages=["http://127.0.0.1/x.html"],
            images=[],
        )
        versions = [
            value
            for label, value in merged.extra_meta
            if label == "Nu HTML Checker version"
        ]
        self.assertEqual(versions, ["26.8.15"])

    def test_drop_snippet_axe_page_chrome(self) -> None:
        from checkmate.html_check import drop_snippet_axe_issues
        from checkmate.models import Issue

        result = CheckResult(
            verdict=Verdict.FAILED,
            errors=2,
            warnings=1,
            issues=[
                Issue(
                    severity=Severity.ERROR,
                    code="document-title",
                    message="need title",
                    source="axe",
                ),
                Issue(
                    severity=Severity.WARNING,
                    code="page-has-heading-one",
                    message="need h1",
                    source="axe",
                ),
                Issue(
                    severity=Severity.ERROR,
                    code="image-alt",
                    message="need alt",
                    source="axe",
                ),
            ],
            tool_name="axe",
        )
        out = drop_snippet_axe_issues(result)
        self.assertEqual([issue.code for issue in out.issues], ["image-alt"])
        self.assertEqual(out.errors, 1)
        self.assertEqual(out.warnings, 0)
        self.assertEqual(out.verdict, Verdict.FAILED)

        chrome_only = CheckResult(
            verdict=Verdict.FAILED,
            errors=1,
            issues=[
                Issue(
                    severity=Severity.ERROR,
                    code="html-has-lang",
                    message="need lang",
                    source="axe",
                )
            ],
            tool_name="axe",
        )
        cleaned = drop_snippet_axe_issues(chrome_only)
        self.assertEqual(cleaned.issues, [])
        self.assertEqual(cleaned.verdict, Verdict.PASSED)


class HtmlSettingsTests(unittest.TestCase):
    def test_defaults_to_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertEqual(settings_mod.html_checkers(), "both")
                self.assertFalse(settings_mod.html_follow_links())
                settings_mod.update_settings(html_checkers="vnu")
                self.assertEqual(settings_mod.html_checkers(), "vnu")
                settings_mod.update_settings(html_checkers="axe")
                self.assertEqual(settings_mod.html_checkers(), "axe")
                settings_mod.update_settings(html_checkers="nope")
                self.assertEqual(settings_mod.html_checkers(), "both")
                settings_mod.update_settings(html_follow_links=True)
                self.assertTrue(settings_mod.html_follow_links())


class RunHtmlCheckAxeTests(unittest.TestCase):
    def test_both_mode_runs_axe_even_if_preflight_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "x.html"
            page.write_text(
                "<!DOCTYPE html><html><body><p>hi</p></body></html>",
                encoding="utf-8",
            )
            fake_vnu = CheckResult(verdict=Verdict.PASSED, tool_name="Nu HTML Checker")
            fake_axe = CheckResult(verdict=Verdict.PASSED, tool_name="axe")
            with (
                mock.patch("checkmate.html_check.html_checkers", return_value="both"),
                mock.patch(
                    "checkmate.html_check.html_follow_links", return_value=False
                ),
                mock.patch(
                    "checkmate.html_check.html_axe_available", return_value=False
                ),
                mock.patch(
                    "checkmate.html_check.run_vnu_on_urls", return_value=fake_vnu
                ),
                mock.patch(
                    "checkmate.html_check.run_axe_on_urls",
                    return_value=(fake_axe, []),
                ) as axe_run,
            ):
                result = run_html_check(str(page))
            axe_run.assert_called_once()
            self.assertEqual(result.tool_name, "Nu HTML Checker + axe")
            self.assertEqual(len(result.html_pages), 1)


class AcePackageRootTests(unittest.TestCase):
    def test_resolves_npm_global_ace_next_to_cmd(self) -> None:
        from checkmate.ace_check import ace_package_from_cli

        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp)
            ace_pkg = prefix / "node_modules" / "@daisy" / "ace"
            (ace_pkg / "node_modules" / "puppeteer").mkdir(parents=True)
            (ace_pkg / "bin").mkdir(parents=True)
            (ace_pkg / "bin" / "ace-puppeteer.js").write_text(
                "// shim", encoding="utf-8"
            )
            cmd = prefix / "ace-puppeteer.cmd"
            cmd.write_text(
                r'"%_prog%" "%dp0%\node_modules\@daisy\ace\bin\ace-puppeteer.js" %*',
                encoding="utf-8",
            )
            resolved = ace_package_from_cli(cmd)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), ace_pkg.resolve())

    def test_system_chrome_is_a_real_file_when_present(self) -> None:
        from checkmate.axe_html import find_system_chrome

        found = find_system_chrome()
        if found is None:
            return
        self.assertTrue(found.is_file())
        self.assertIn("chrome", found.name.lower())

    def test_ignores_system_nodejs_dir(self) -> None:
        from checkmate.ace_check import ace_package_from_cli

        with tempfile.TemporaryDirectory() as tmp:
            nodejs = Path(tmp) / "nodejs"
            nodejs.mkdir()
            (nodejs / "node.exe").write_bytes(b"")
            self.assertIsNone(ace_package_from_cli(nodejs / "node.exe"))


if __name__ == "__main__":
    unittest.main()
