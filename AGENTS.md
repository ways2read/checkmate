# AGENTS.md

## Cursor Cloud specific instructions

CheckMate is a **wxPython desktop GUI app** (no web server, no automated test
suite). It wraps the Java command-line checkers eBraille Checker, EPUBCheck, and
veraPDF. See `README.md` for full feature/usage details and the canonical
commands; the notes below only capture non-obvious, environment-specific gotchas.

### Environment layout

- Python deps live in a `.venv` at the repo root (Python 3.12). The startup
  update script rebuilds/refreshes it. Run things with `.venv/bin/python ...`.
- `wxPython` is installed from the **prebuilt Linux wheel** at
  `https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-24.04/`, not
  from PyPI. PyPI ships no Linux wheel for the pinned version, so a plain
  `pip install wxPython` / `uv sync` would compile it from a ~58 MB sdist (slow,
  needs GTK build deps). The update script forces the wheel with
  `--only-binary wxPython --find-links <that url>`.
- System libraries the wheel needs at runtime are already present in the VM
  snapshot (installed via `apt`, not the update script): `libwebkit2gtk-4.1-0`
  (for the optional `wx.html2.WebView` used by "Explain with AI"; the app falls
  back to a text control without it), `python3.12-venv`, plus GTK3, GStreamer,
  SDL2, and libnotify which ship with the desktop image.

### Running the app

- The GUI requires a display. A virtual display is available at `DISPLAY=:1`.
  Launch with: `DISPLAY=:1 .venv/bin/python run.py` (equivalently
  `python -m checkmate` / the `checkmate` entry point from `README.md`).
- On first launch CheckMate downloads eBraille Checker and EPUBCheck from GitHub
  into `~/.local/share/CheckMate/` on a background thread (needs network).
  veraPDF is only downloaded on the first PDF check. These downloads persist, so
  later runs start offline-capable. A first-run "Update available" dialog for
  veraPDF is expected — dismiss it unless you are testing PDFs.
- Java is required (JRE 17+). The VM's system Java (currently 21) is detected
  automatically; no bundled `runtime/` is needed when running from source.

### Testing / linting

- There is **no committed lint/format/test config** (no ruff/flake8/mypy/pytest
  setup) and no test directory. `# noqa` comments imply ruff is used informally
  upstream, but nothing is configured here. The closest available static check
  is `.venv/bin/python -m compileall checkmate run.py scripts`.
- To validate a publication without the GUI (useful headless smoke test):
  `from checkmate.checker import run_check` then `run_check(Path("file.epub"))`
  returns a result with a `.verdict` (e.g. `Verdict.PASSED`) and `.issues`.
- `testdata/` is gitignored and empty by design — drop sample
  `.epub` / `.ebrl` / `.pdf` files there. `scripts/make_test_epub.py` writes a
  minimal valid sample EPUB for quick checks.
