# CheckMate

An accessible, cross-platform desktop front-end for the
[DAISY eBraille Checker](https://github.com/daisy/ebraille-checker),
[W3C EPUBCheck](https://github.com/w3c/epubcheck), and
[veraPDF](https://verapdf.org/) (PDF/UA).

Those checkers are Java command-line tools. This app wraps them so you can open
a publication and see a clear result — **Passed**, **Passed with warnings**, or
**Failed** — without typing `java -jar` commands or reading a long console log
first. `.ebrl` files use eBraille Checker; `.epub` files use stock EPUBCheck
plus Ace by DAISY when available; `.pdf` files use veraPDF against the PDF/UA
profile chosen in **Tools → Settings…** (default PDF/UA-2);
exploded folders are classified automatically.

Built with [wxPython](https://wxpython.org/) for native widgets and screen reader
support on Windows, macOS, and Linux.

## Features

- Open a packaged `.ebrl`, `.epub`, or `.pdf` file, or an exploded publication
  folder (**Select file…** / **Select folder…**, or drag and drop) — checking
  starts automatically with the matching engine
- For `.epub` files, EPUBCheck always runs; [Ace by DAISY](https://daisy.github.io/ace/)
  runs next and results are merged into one list (tagged by source). Packaged
  builds bundle Ace (with its own Node runtime and Chromium); otherwise a
  user-installed CLI is used (`ace-puppeteer` preferred, or `ace`). If Ace is
  not available, EPUBCheck-only behavior is unchanged
- On Windows, right-click an `.ebrl`, `.epub`, or `.pdf` → **Validate with
  CheckMate**, or **Open with** → CheckMate (does not change the
  double-click default)
- On macOS packaged builds, Finder **Open With** for `.ebrl` / `.epub` / `.pdf`
  (does not take over double-click by default)
- Result-first UI: multi-line verdict with counts; colour cues (green / orange /
  red) reinforce the text; a status icon beside the result (click to select a
  file); action column for **Copy summary**, **Report…**, **AI overview**,
  **Alt text**, and **Show/Hide issues**; issues listed by severity (panel
  starts collapsed)
- Filter issues (all / errors / warnings / info); optional **Show one example
  of each issue** to collapse repeated codes with counts
- Filter by source (**EPUBCheck + Ace**, or either tool alone) when both ran
  (Tools → Settings… chooses EPUBCheck + Ace by default, or either tool alone)
- Ace issue details show **Impact** (critical / serious / moderate / minor) and
  **Ruleset** (e.g. WCAG 2.0 A, EPUB, Best Practice), matching the Ace app
- Optional full checker log for advanced diagnosis
- **Explain with AI** (when FIDO AI settings are present on this machine): open
  an issue’s details and ask for a structured plain-language briefing plus
  follow-up questions. Uses FIDO’s API keys and/or unlock code (keys from
  unlock stay in memory only). Validation itself stays offline. Requests show
  a cancellable progress dialog, check the provider connection first, and
  write diagnostics to the app log (**Help → Open debugging log…**).
- **AI overview** (same FIDO AI gate): dedicated **AI overview** button (and
  **Report** menu when AI is enabled); whole-report briefing — themes,
  priorities, and next steps based on the unique issue codes (not a full file
  dump). View, save, or copy the result like other AI replies. Toggle under
  **Tools → Settings…** (hidden when FIDO AI is unavailable).
- **Alt text** (packaged EPUB / eBraille / PDF after a check): **Alt text**
  button after AI overview opens an in-app inventory of images and alt text
  (CheckMate-branded export, cached on disk per publication). From that report, optional
  **Run AI Image Sniff Test…** samples images with vision and synthesizes
  findings (same FIDO AI credentials) — including alt quality, decorative vs
  content, images of tables (prefer real table markup), images of math (prefer
  digital math such as LaTeX/MathML/OMML, or PDF equation images tagged with
  MathML), low-resolution rasters that fail under magnification, likely wrong
  orientation, joined/multi-panel figures, alt that repeats nearby prose,
  language mismatch, and spelling or grammar. Guidance is format-specific
  (PDF vs EPUB vs eBraille). The HTML report can be filtered, searched, and
  sorted, and names the model in the disclaimer. PDF image previews follow
  on-page crop and rotation. Several images are assessed in parallel (eight
  at a time by default; fewer after a rate limit); the
  progress dialog shows how many are done, running Likely OK / Needs
  attention counts, which files are in flight, and an
  estimated time remaining, can be cancelled, and is spoken to screen readers.
- **Suggest fix with AI** (EPUB and eBraille only, when FIDO AI settings are present):
  from the same issue details dialog, ask for a minimal suggested markup patch,
  preview before/after, then **Apply fix and validate** to the exploded folder or packaged
  `.epub`/`.ebrl` (creates a `.bak` backup).   When the report has more than one
  issue with the same checker code, **Suggest fix for many** suggests up to 20
  unique replacements across matching instances in one backup/rebuild cycle.
  The details dialog closes and the
  publication is re-checked automatically. CheckMate then reports whether the
  targeted issue is gone, whether overall error/warning counts decreased, and
  (for Ace fixes) whether any new EPUBCheck errors appeared; if anything looks
  wrong, it offers to **Revert** or **Keep** the change. Each applied fix also appends an
  entry to a **edit changelog** beside the publication
  (`book.epub.checkmate-changelog.md`, or `checkmate-changelog.md` inside an
  exploded folder) naming the backup file, the issue fixed, and how to revert.
  Open it from **Report → View edit changelog…** when present.
  Packaged rewrite uses the same EPUB-safe extract/rebuild approach as FIDO.
- Copy summary; view or save text / HTML reports (**Report** menu) — HTML
  reports embed an EPUB/eBraille cover image when present, or the first page
  of a PDF; reports and the result pane name the validation profile / ruleset
  that ran (PDF/UA, ebraille, Ace axe-core); **Clear results**
  returns to the launch state
- **Tools → Settings…**: general preferences (show issues always, sound
  scheme, color theme — System / Light / Dark, AI features) and the veraPDF
  PDF/UA profile (UA-1 or UA-2). eBraille always uses the `ebraille`
  EPUBCheck profile. System follows the computer light/dark setting; Light
  and Dark keep CheckMate on that theme (including HTML reports and
  progress-dialog text).
- Status bar shows installed checker versions, and Ace / Pipeline when detected
- UI languages: English plus shipped catalogs (Français, Español, العربية,
  Русский, 日本語) and user-added languages (remembered; first run follows the
  OS language when supported; AI replies follow the selected language). Users
  can generate additional catalogs in-app; validated ones may be added to the
  installer later. Non-English languages can be managed from the Language menu
  (edit strings, AI update/regenerate, export, or hide until re-added).
  Catalogs use the `checkmate-ui-i18n` JSON format (including LTR/RTL
  direction).
- Downloads eBraille Checker and EPUBCheck on first run when not bundled;
  downloads veraPDF on first PDF check when not bundled
- In-app update check for all tools; updates install to application data and
  leave the bundled install-folder copies untouched
- Uses `-Xss4m` when launching Java to avoid known stack overflow crashes on
  smaller JREs
- **Packaged builds** can include bundled Eclipse Temurin JRE, eBraille Checker,
  EPUBCheck, Ace by DAISY, and veraPDF (works offline on first launch)

## Requirements

### Running from source (developers)

- **Python** 3.10 or newer
- **Java** Runtime (JRE 17+ recommended) on your `PATH`, *or* a local `runtime/`
  folder (see packaging below)
- **Network** on first launch (to download checkers when not bundled), and when
  checking for updates

Jars are fetched from
[daisy/ebraille-checker releases](https://github.com/daisy/ebraille-checker/releases),
[w3c/epubcheck releases](https://github.com/w3c/epubcheck/releases), and
[veraPDF downloads](https://software.verapdf.org/rel/).

### Running a packaged build (end users)

- No system Java required — the distribution includes `runtime/` with Temurin JRE 17
- No download required on first run when `checker/`, `epubcheck/`, and `verapdf/`
  are bundled
- Network only needed when checking for tool updates (or if built with
  `--no-bundle-checker` / `--no-bundle-epubcheck` / `--no-bundle-verapdf`)

## Install (developers)

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
git clone https://github.com/ways2read/checkmate.git
cd checkmate
uv sync
```

With pip:

```bash
git clone https://github.com/ways2read/checkmate.git
cd checkmate
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

## Run

```bash
uv run python run.py
# or, with venv activated:
python run.py
python -m checkmate
# or:
uv run checkmate
```

## Using the app

1. **Select file…** or **Select folder…**, or **drag and drop** a publication
   onto the window — checking starts automatically. On Windows you can also
   right-click an `.ebrl`, `.epub`, or `.pdf` → **Validate with CheckMate**,
   or **Open with** → CheckMate. On macOS, use Finder **Open With** for a
   packaged `.app`.
2. While a check runs, the **Result** pane shows living progress (Ace streams
   document status; other tools show elapsed time). When finished, focus moves
   to the summary; expand **Show issues** to review the list (filterable), or enable
   **Show issues always** under **Tools → Settings…** to open the list automatically
   after checks that find issues.
   Short sounds play when a check starts and when it finishes (passed vs
   failed); choose **Sound scheme 1**, **Sound scheme 2**, or **Sounds off**
   under **Tools → Settings…**.
3. Use **Report → View full log** (`Ctrl+L`) only when you need the raw
   checker output.
4. **Tools → Re-check publication** (`F5`) re-runs the current path after you
   fix issues.
5. **Report → Clear results** (`Ctrl+Shift+N`) clears the path, verdict, issues,
   and log back to the launch state.
6. **Tools → Check for updates…** offers to download newer eBraille Checker,
   EPUBCheck, and/or veraPDF releases when they exist.

The **title bar** keeps the app name and appends the verdict (for example
`CheckMate — Failed — 3 errors`). The **status bar** shows tool
versions and Java information.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Select file |
| `Ctrl+Shift+O` | Select folder |
| `Ctrl+Tab` | Leave AI explanation WebView (details/overview dialogs) |
| `F5` | Re-check current publication |
| `Ctrl+T` | View text report |
| `Ctrl+Shift+S` | Save text report |
| `Ctrl+H` | View HTML report in browser |
| `Ctrl+S` | Save HTML report |
| `Ctrl+Shift+C` | Copy summary |
| `Ctrl+Shift+N` | Clear results |
| `Ctrl+L` | View full log |
| `Esc` | Exit |
| Enter (in path field) | Check the path currently shown |
| Alt+letter | Button / menu mnemonics (underlined letters) |

### Where data is stored

**Checkers** — for each tool (eBraille Checker, EPUBCheck, and veraPDF), the app
uses the newest available copy in this order:

1. **Updated copy** in application data (after you accept an in-app update)
2. **Bundled copy** shipped with the packaged app (`checker/`, `epubcheck/`, or
   `verapdf/` next to the executable)
3. **Downloaded copy** on demand (eBraille/EPUBCheck on first run when missing;
   veraPDF on first PDF check)

| OS | Application data |
|---|---|
| Windows | `%LOCALAPPDATA%\CheckMate\` |
| macOS | `~/Library/Application Support/CheckMate/` |
| Linux | `~/.local/share/CheckMate/` |

Under that folder:

- `checker/` — downloaded or updated eBraille Checker releases
- `epubcheck/` — downloaded or updated EPUBCheck releases
- `verapdf/` — downloaded or updated veraPDF CLI installs
- `settings.json` — remembered UI language and preferences (e.g. Show issues always, sound scheme, veraPDF profile, hidden languages)
- `i18n/` — editable UI language catalogs (`checkmate-ui-i18n` JSON overlays)

Packaged builds also include `checker/`, `epubcheck/`, and `verapdf/` beside the
executable (or inside the `.app` bundle on macOS).

## Accessibility

- Native wxPython controls (menus, buttons, list, text fields)
- Logical focus order; the **Result** pane is a large, bold, focusable
  read-only multi-line field so screen readers can tab in and re-read with the
  caret (Up/Down by line, Left/Right by character)
- **Spoken status** (Windows, NVDA/JAWS via `accessible-output2`): first-use
  prep, Ready, check start, each checker start, living progress after 5s
  elapsed, and “Check finished…” when results arrive. Updates interrupt so
  stale phrases do not queue. AI/overview progress dialogs speak phase changes
  the same way (animation-only pulses stay silent).
- When a check finishes, the main window is raised and focus stays in Result
  so the verdict is also reachable by keyboard. Mid-check milestones quietly
  focus Result; living timer lines update Result without stealing focus. Set
  `_ANNOUNCE_MODE = "selection"` in `main.py` for the older leave-text-selected
  behaviour.
- **Explain with AI** prefers an Edge/WebKit `WebView` so the reply is real HTML
  (headings, lists, links) for screen readers; falls back to a Markdown text
  field if no webview backend is available (`HtmlWindow` is not used). The
  details/overview dialogs keep the WebView in the Tab cycle so focus can
  return after leaving the pane. After Explain/Fix, the dialog reclaims
  foreground focus so the explanation is reachable without Alt+Tab. Inside the
  explanation, Tab starts at the top then moves between links (keyboard focus
  is armed without a mouse click); Tab after the last link (or **Ctrl+Tab**)
  leaves the WebView for the next dialog control. **Escape** closes the dialog
  from the WebView host or from inside the page (`checkmate://close`).
- Accessible name includes the verdict text; the window title also appends it
- **Language** menu: each installed language is a submenu with **Select** (and,
  for non-English, edit / update / regenerate / export / remove). **Add
  language…** / **Import language…** add catalogs.
- Severity and pass/fail are always in text; result colour is only a visual cue

Designed for use with NVDA, JAWS, Narrator, and VoiceOver. Feedback on
accessibility gaps is welcome via GitHub issues.

## Equivalent command line

This app runs the same checkers you would invoke manually.

eBraille (packaged):

```bash
java -Xss4m -jar path\to\ebraille-checker.jar --profile ebraille publication.ebrl
```

eBraille (exploded folder):

```bash
java -Xss4m -jar path\to\ebraille-checker.jar -mode exp --profile ebraille path\to\folder
```

EPUB (packaged):

```bash
java -Xss4m -jar path\to\epubcheck.jar publication.epub
```

EPUB (exploded folder):

```bash
java -Xss4m -jar path\to\epubcheck.jar -mode exp path\to\folder
```

PDF (veraPDF flavour from **Tools → Settings…**, default PDF/UA-2; falls back
to PDF/UA-1 if veraPDF crashes on UA-2 when that profile was selected):

```bash
java -Djava.awt.headless=true -jar path\to\cli-*.jar --flavour ua2 --format json publication.pdf
```

`-Xss4m` increases the Java thread stack size. Without it, some publications can
trigger `java.lang.StackOverflowError` during RelaxNG validation on smaller JREs.

## Troubleshooting

### “Java was not found”

**Packaged build (Windows):** use the full `dist/CheckMate/` folder (or the
Inno Setup installer). It must contain a `runtime/` directory next to the
executable. Do not copy only the `.exe` without the rest of the folder.

**Packaged build (macOS):** the `.app` includes `Contents/runtime/` with Temurin
JRE. If checks fail with “Java was not found” even though `runtime/` is present,
the bundle was almost certainly signed without the JVM entitlements in
`packaging/macos/entitlements.plist` (`allow-jit` and
`allow-unsigned-executable-memory`). Reinstall from a build produced by
`scripts/build_macos_release.sh` (do not sign the app by hand without that
plist). As a temporary workaround, install a system JRE 17+.

**From source:** install a JRE or JDK (17+ recommended), ensure `java -version`
works in a terminal, then restart. Or download a local runtime:

```bash
uv run python scripts/jre_bundle.py
```

The app prefers `runtime/bin/java` (bundled) over Java on your `PATH`.

### `StackOverflowError` when running the jar yourself

Add `-Xss4m` (or `-Xss8m`) before `-jar`, as shown above. The GUI already does this.

### Checker download fails

Check your network and download-site availability, then use
**Tools → Download / reinstall checkers…**, or install tools manually:

- eBraille Checker / EPUBCheck: download release zips from
  [eBraille Checker releases](https://github.com/daisy/ebraille-checker/releases)
  and [EPUBCheck releases](https://github.com/w3c/epubcheck/releases) and extract
  into the application data `checker/` or `epubcheck/` folders listed above
- veraPDF: download the greenfield installer from
  [software.verapdf.org/rel](https://software.verapdf.org/rel/) and install into
  the application data `verapdf/` folder (the app expects `bin/cli-*.jar`)

### Extension case (`.eBRL` vs `.ebrl`)

The checker may report that a packaged eBraille file must use the lowercase
extension `.ebrl`. Rename the file if needed.

### macOS DMG says “newer version already installed”

The drag-to-Applications DMG is not a wizard installer — Finder refuses to
replace the app when the copy in `/Applications` has a higher `CFBundleVersion`
build number than the one in the disk image (common when reinstalling the same
marketing version from an older DMG).

Remove the existing app first (`Applications` → move `CheckMate.app` to
Trash → empty Trash), then drag the new app from the DMG onto Applications
again. Each release build from `scripts/build_macos.sh` increments
`build_counter.txt` so newer DMGs upgrade cleanly.

## Project layout

```text
checkmate/
  checkmate/
    main.py            # wxPython UI
    accessibility.py   # Screen-reader speak helpers (accessible-output2)
    checker.py         # Run jar, parse JSON results
    cover_image.py     # EPUB cover / PDF first-page for HTML reports
    publication.py     # Classify .ebrl / .epub / .pdf / exploded folders
    epub_package.py    # Extract / rebuild .epub/.ebrl (Fix with AI apply)
    updater.py         # Tool download / update (GitHub + veraPDF installer)
    java_util.py       # Locate Java (bundled or PATH)
    models.py          # Verdict and issue models
    report_export.py   # Text / HTML report export
    telemetry.py       # FIDO-consent usage telemetry (shared secrets/sender)
    i18n.py            # UI language registry + catalog load/merge
    i18n_ai.py         # AI translation of UI language catalogs
    locales/           # Shipped checkmate-ui-i18n JSON catalogs (*.json)
    settings.py        # Persisted preferences
    settings_dialog.py # Tools → Settings… (prefs + validation profiles)
    paths.py           # App data and bundle locations
    subprocess_util.py # Quiet subprocess helpers (Windows)
    fido_settings.py   # Read FIDO AI keys/models (no FIDO import)
    logging_setup.py   # App-data log file (Help → Open debugging log)
    ai/                # Explain with AI / Fix with AI
  run.py               # Launcher (incl. SSL cert setup when frozen)
  scripts/
    package.py               # PyInstaller + bundled JRE, checker, EPUBCheck, veraPDF
    jre_bundle.py            # Download Temurin JRE into runtime/
    checker_bundle.py        # Download eBraille Checker into checker/
    epubcheck_bundle.py      # Download EPUBCheck into epubcheck/
    verapdf_bundle.py        # Download/install veraPDF into verapdf/
    build_installer.ps1      # Windows: package + Inno Setup compile + Authenticode
    build_macos.sh           # macOS: package .app + zip
    build_macos_dmg.sh       # macOS: drag-to-Applications .dmg
    build_macos_release.sh   # macOS: sign + .dmg + notarize
    make_icns.py             # Build .icns/.ico from installer/icon.png (blue on white)
    macos_release_arch_suffix.inc.sh
  installer/
    CheckMate.iss         # Inno Setup script (Windows installer)
    CheckMate.ico         # App / setup icon (Windows)
    CheckMate.icns        # App / volume icon (macOS; rebuilt from icon.png)
    icon.png              # Blue CheckMate mark on white (macOS .icns master)
    welcome.txt           # Setup wizard intro text
  packaging/macos/
    entitlements.plist    # Hardened runtime + JVM entitlements (required)
    dmg_background.png    # Drag-install DMG window background
    make_dmg_background.py
  pyproject.toml
  requirements.txt
  requirements-dev.txt
  testdata/            # Optional local sample publications (not shipped)
```

## Packaging

Build a standalone app on each target OS (Windows or macOS). The script bundles
**Eclipse Temurin JRE 17**, **eBraille Checker**, **EPUBCheck**, **veraPDF**,
and **Ace by DAISY** by default. **PyMuPDF** is included in the Python app bundle (via PyInstaller
`--collect-all pymupdf`) on **both Windows and macOS** so HTML reports can show
PDF first-page previews — no separate macOS packaging step is required beyond
`scripts/package.py` / `scripts/build_macos.sh`.

Ace by DAISY is bundled as a self-contained `ace/` directory
(`scripts/ace_bundle.py`): a portable Node.js LTS runtime, `@daisy/ace-cli`
(the Puppeteer runner — same CLI, no Electron), and a pinned Chrome for
Testing in a private Puppeteer cache. The build machine does not need Node —
the downloaded portable Node's own npm performs the install. At run time the
bundled copy is preferred; without it, builds fall back to a user install of
`ace-puppeteer` (preferred) or `ace` on `PATH`, also checking common npm /
Homebrew install locations (`~/.npm-global/bin`, `/usr/local/bin`,
`/opt/homebrew/bin`, …) so a Finder-launched macOS `.app` can still find a
user install. The default Electron `ace` CLI can fail when
`ELECTRON_RUN_AS_NODE` is set (e.g. under some Electron hosts); the app clears
that variable and prefers the Puppeteer runner.

```bash
uv sync --extra dev
uv run python scripts/package.py --clean
```

Options:

```bash
uv run python scripts/package.py --no-bundle-java       # smaller build; needs system Java
uv run python scripts/package.py --no-bundle-checker    # eBraille Checker on first run
uv run python scripts/package.py --no-bundle-epubcheck  # EPUBCheck on first run
uv run python scripts/package.py --no-bundle-verapdf    # veraPDF on first PDF check
uv run python scripts/package.py --no-bundle-ace        # needs user install of @daisy/ace
uv run python scripts/package.py --onefile              # not recommended with bundles
```

Output layout (Windows example):

```text
dist/CheckMate/
  CheckMate.exe
  runtime/              # bundled Temurin JRE
    bin/java.exe
  checker/              # bundled eBraille Checker
    bundled_version.txt
    …/ebraille-checker.jar
  epubcheck/            # bundled EPUBCheck
    bundled_version.txt
    …/epubcheck.jar
  verapdf/              # bundled veraPDF CLI
    bundled_version.txt
    bin/cli-*.jar
  ace/                  # bundled Ace by DAISY
    bundled_version.txt
    node/node.exe       # portable Node.js runtime
    node_modules/@daisy/ace-cli/bin/ace.js
    puppeteer/          # pinned Chrome for Testing
  … (PyInstaller support files)
```

On Windows, prefer the **Inno Setup** installer (below) for end users. You can
still zip and distribute the entire `dist/CheckMate/` folder if needed —
do not ship only the `.exe`.

On macOS, prefer the **signed and notarized `.dmg`** (below). You can still
distribute the `.app` zip from `scripts/build_macos.sh` if needed.

When a newer eBraille Checker, EPUBCheck, or veraPDF is released, **Tools → Check for
updates…** compares against the versions in use (bundled or previously updated).
Accepting an update downloads the new release(s) into application data; the
bundled copies in the install folder are not modified.

### Windows installer (Inno Setup)

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php). Keep
`MyAppVersion` in `installer/CheckMate.iss` in sync with
`pyproject.toml` / `checkmate/__init__.py`.

One-shot (packages the app, then compiles the setup):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

Or step by step:

```powershell
uv sync --extra dev
uv run python scripts/package.py --clean
# Then compile with Inno Setup Compiler, or:
iscc installer\CheckMate.iss
```

Output: `installer/Output/CheckMate-<version>-setup.exe`

`build_installer.ps1` then Authenticode-signs that setup with the same
`signtool` flow as Fido (`/a`, SHA-256, GlobalSign RFC 3161 timestamp) so
SmartScreen sees a signed installer. That needs the Windows SDK App
Certification Kit and the USB signing token (one PIN prompt). Skip with
`-SkipSign` or `CHECKMATE_SKIP_INSTALLER_SIGN=1`. Override the tool or TSA
with `CHECKMATE_SIGNTOOL` / `CHECKMATE_SIGN_TIMESTAMP_URL`.

The installer:

- Ships the full onedir tree (GUI + Temurin JRE 17 + eBraille Checker +
  EPUBCheck, veraPDF, Ace, status icons) — no system Java
  (`build_installer.ps1` refuses to compile if `runtime/`, `checker/`,
  `epubcheck/`, `verapdf/`, or `ace/` is missing from `dist/`)
- Supports per-user install (default) or Program Files with elevation
- Adds `.ebrl` / `.epub` / `.pdf` shell integration (optional task, on by default):
  **Open with** → CheckMate, and context menu **Validate with
  CheckMate** — does not change the double-click default for any
  extension (EPUB/PDF readers stay as the default opener)
- Offers an optional desktop shortcut and launch-on-finish
- On uninstall, optionally removes `%LOCALAPPDATA%\CheckMate\`
  (settings and checker/EPUBCheck updates)

### macOS disk image + notarization

Pattern is: build an `.app`, wrap it in a drag-to-Applications
`.dmg`, then **codesign** and **notarize** so Gatekeeper accepts the download.

Prerequisites:

- Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/)
- A **Developer ID Application** certificate in your login keychain
- Notary credentials (one of):
  - Keychain profile: `xcrun notarytool store-credentials "fido-notary" …`
    (CheckMate falls back to this Fido profile when `ebraille-notary` is missing)
  - Or App Store Connect API key (`AuthKey_*.p8` + key id + issuer)

**Signing must use `packaging/macos/entitlements.plist`.** That plist enables
hardened-runtime library loading for PyInstaller **and** the JVM entitlements
(`allow-jit`, `allow-unsigned-executable-memory`) required for the bundled
Temurin JRE. Signing without them makes `runtime/bin/java` crash (`SIGTRAP`),
and the GUI reports Java as missing. `scripts/build_macos_release.sh` applies
this plist automatically. PyMuPDF native libraries collected into the app
bundle are signed with the rest of `Contents/` by the same release script.

One-shot release (package → sign → DMG → notarize → staple):

```bash
chmod +x scripts/build_macos_release.sh
EBC_NOTARY_PROFILE=fido-notary ./scripts/build_macos_release.sh
# optional explicit version:
EBC_NOTARY_PROFILE=fido-notary ./scripts/build_macos_release.sh 0.2.2
```

Outputs (arch suffix is `-AppleSilicon` or `-Intel`):

- `dist/CheckMate_App/CheckMate.app`
- `dist/CheckMate-macOS-<version>.<build>-<arch>.zip`
- `dist/CheckMate-macos-<version>.<build>-<arch>.dmg` (signed + notarized when credentials are set)

The finished `.dmg` is also copied to the shared **development builds** folder as
`setupcheckmate_<version>.<build>-<arch>.dmg` (override with `EBC_DEV_BUILDS_DIR`,
or skip with `EBC_SKIP_ONEDRIVE_COPY=1`).

Step by step:

```bash
./scripts/build_macos.sh 0.2.2          # .app + zip
./scripts/build_macos_dmg.sh 0.2.2      # drag-install .dmg (unsigned)
```

App icon: macOS packaging rebuilds `installer/CheckMate.icns` from
`installer/icon.png` (blue CheckMate mark on a white background). The green
PNGs in `images/` are in-app status graphics only.

Useful environment variables:

| Variable | Meaning |
|----------|---------|
| `EBC_NOTARY_PROFILE` | Keychain profile for `notarytool` |
| `EBC_NOTARY_KEY` / `EBC_NOTARY_KEY_ID` / `EBC_NOTARY_ISSUER` | API-key notary credentials |
| `EBC_APP_SIGN_IDENTITY` | Override Developer ID Application identity |
| `EBC_SKIP_NOTARY=1` | Build and sign only (no notarization) |
| `EBC_SKIP_APP_SIGN=1` | Skip codesign (local smoke builds) |
| `EBC_SKIP_APPLICATION_BUILD=1` | Re-sign / notarize an existing `dist/CheckMate_App/` |
| `EBC_DEV_BUILDS_DIR` | Folder for the versioned `.dmg` copy (default: OneDrive development builds) |
| `EBC_SKIP_ONEDRIVE_COPY=1` | Do not copy the `.dmg` to the development builds folder |

**Upgrading / reinstalling:** macOS compares `CFBundleVersion` (an integer build
number from `build_counter.txt`, bumped on each `build_macos.sh` run) when you
drag the app onto Applications. If Finder says a newer version is already
installed, remove `CheckMate.app` from Applications first (Trash → empty
Trash), then drag again. The DMG also includes `Install CheckMate.txt`
with these steps.

`scripts/package.py` registers `.ebrl`, `.epub`, and `.pdf` document types in the
`.app` `Info.plist` with rank **Alternate**, so the app appears under Finder
**Open With** without becoming the default double-click handler. Opening a
file that way launches the GUI and starts a check automatically.

## Test data

Place your own `.ebrl` / `.epub` / `.pdf` files or exploded folders under `testdata/` for
local testing. Sample publications are **not** included in the repository. See
[`testdata/README.md`](testdata/README.md) for folder detection notes.

## Credits

- eBraille conformance checking is performed by
  [eBraille Checker](https://github.com/daisy/ebraille-checker) from the
  [DAISY Consortium](https://daisy.org/), based on EPUBCheck.
- EPUB conformance checking is performed by
  [EPUBCheck](https://github.com/w3c/epubcheck) (W3C / DAISY).
- PDF/UA checking is performed by [veraPDF](https://verapdf.org/).
- When the [Ace by DAISY](https://daisy.github.io/ace/) CLI is installed, EPUB
  accessibility checks are merged with EPUBCheck results.
- PDF first-page previews in HTML reports use
  [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`).
- Learn about the [eBraille standard](https://daisy.org/activities/standards/ebraille/)
  and the [eBraille specification](https://daisy.org/s/ebraille/).
- CheckMate is a separate front-end project and is not an official DAISY release.

## License

This project (CheckMate) is released under the [MIT License](LICENSE).

The eBraille Checker, EPUBCheck, and veraPDF tools downloaded at runtime remain
under their own licenses; see the
[eBraille Checker](https://github.com/daisy/ebraille-checker),
[EPUBCheck](https://github.com/w3c/epubcheck), and
[veraPDF](https://verapdf.org/) projects.
