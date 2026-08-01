"""One-time warm-up of external tools after install or upgrade.

The first execution of freshly installed binaries is slow: antivirus
real-time protection (Windows Defender in particular) scans the bundled
JRE, the checker jars, and — for Ace — Node plus Puppeteer's Chromium the
first time each runs. That cost otherwise lands on the user's first real
check, which then looks hung. Running every tool once in the background
right after install/upgrade moves the scans to startup instead.
"""

from __future__ import annotations

import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__
from .java_util import cached_java
from .paths import app_data_dir, find_checker_jar, find_epubcheck_jar
from .subprocess_util import hidden_run_kwargs

ProgressCallback = Callable[[str], None]

_MARKER_NAME = "warmup_done.txt"
_JAR_TIMEOUT = 300
_STUB_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:00000000-0000-0000-0000-000000000000</dc:identifier>
    <dc:title>Warm-up</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="nav"/>
  </spine>
</package>
"""
_STUB_NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head><title>Warm-up</title></head>
<body>
<nav epub:type="toc">
<h1>Contents</h1>
<ol><li><a href="nav.xhtml">Start</a></li></ol>
</nav>
</body>
</html>
"""
_STUB_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _marker_path() -> Path:
    return app_data_dir() / _MARKER_NAME


def _expected_marker() -> str:
    """Stamp identifying what has been warmed.

    Includes the app version (an upgrade replaces the bundled tools, which
    resets the AV scan cache for them) and the resolved Ace command (so a
    newly installed or relocated Ace triggers a fresh warm-up).
    """
    from .ace_check import ace_command

    ace = ace_command()
    ace_id = " ".join(ace) if ace else "no-ace"
    return f"{__version__}\n{ace_id}\n"


def warmup_needed() -> bool:
    try:
        return _marker_path().read_text(encoding="utf-8") != _expected_marker()
    except OSError:
        return True


def _write_marker() -> None:
    try:
        _marker_path().write_text(_expected_marker(), encoding="utf-8")
    except OSError:
        pass


def _warm_jar(jar: Path | None) -> None:
    """Run a jar once (``--version``) so its files get read and scanned."""
    if jar is None:
        return
    java = cached_java()
    if java is None:
        return
    try:
        subprocess.run(
            [java.path, "-jar", str(jar), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_JAR_TIMEOUT,
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _write_stub_epub(path: Path) -> None:
    """Minimal valid EPUB 3 so Ace parses it and actually launches Chromium."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        zf.writestr("META-INF/container.xml", _STUB_CONTAINER)
        zf.writestr("EPUB/package.opf", _STUB_OPF)
        zf.writestr("EPUB/nav.xhtml", _STUB_NAV)


def _warm_ace() -> None:
    """Run a full Ace check on a stub EPUB (spawns Node + Chromium once)."""
    from .ace_check import ace_command, run_ace_check

    if ace_command() is None:
        return
    try:
        with tempfile.TemporaryDirectory(prefix="ebraille-warmup-") as tmp:
            stub = Path(tmp) / "warmup.epub"
            _write_stub_epub(stub)
            run_ace_check(stub)
    except Exception:  # noqa: BLE001 — warm-up must never break startup
        pass


def run_startup_warmup(progress: ProgressCallback | None = None) -> None:
    """Warm all tools once after install/upgrade. Safe to call every startup."""
    if not warmup_needed():
        return
    from .i18n import _

    if progress:
        progress(_("Preparing checkers for first use (one-time, may take a minute)…"))
    _warm_jar(find_epubcheck_jar())
    _warm_jar(find_checker_jar())
    _warm_ace()
    _write_marker()
