# eBraille Checker GUI

An accessible, cross-platform desktop front-end for the
[DAISY eBraille Checker](https://github.com/daisy/ebraille-checker).

The official checker is a Java command-line tool. This app wraps it so you can
open a publication and see a clear result — **Passed**, **Passed with warnings**,
or **Failed** — without typing `java -jar` commands or reading a long console log
first.

Built with [wxPython](https://wxpython.org/) for native widgets and screen reader
support on Windows, macOS, and Linux.

## Features

- Open a packaged `.ebrl` file, or an exploded publication folder
  (**Select file…** / **Select folder…**, or drag and drop) — checking starts
  automatically
- On Windows, right-click an `.ebrl` → **Validate with eBraille Checker**, or
  **Open with** → eBraille Checker (does not change the double-click default)
- On macOS packaged builds, Finder **Open With** for `.ebrl` (does not take over
  double-click by default)
- Result-first UI: multi-line verdict with counts; colour cues (green / orange /
  red) reinforce the text; issues listed by severity
- Filter issues (all / errors / warnings / info)
- Optional full checker log for advanced diagnosis
- Copy summary or save a text report; **Clear results** returns to the launch state
- UI languages: English, Français, Español, Deutsch, Português (remembered;
  first run follows the OS language when supported)
- Downloads the eBraille Checker on first run when not bundled
- In-app update check; updates install to application data and leave the bundled
  install-folder copy untouched
- Uses `-Xss4m` when launching Java to avoid known stack overflow crashes on
  smaller JREs
- **Packaged builds** can include bundled Eclipse Temurin JRE and eBraille
  Checker (works offline on first launch)

## Requirements

### Running from source (developers)

- **Python** 3.10 or newer
- **Java** Runtime (JRE 17+ recommended) on your `PATH`, *or* a local `runtime/`
  folder (see packaging below)
- **Network** on first launch (to download the checker), and when checking for
  updates

The checker jar is fetched from
[daisy/ebraille-checker releases](https://github.com/daisy/ebraille-checker/releases).

### Running a packaged build (end users)

- No system Java required — the distribution includes `runtime/` with Temurin JRE 17
- No download required on first run when `checker/` is bundled with the app
- Network only needed when checking for checker updates (or if built with
  `--no-bundle-checker`)

## Install (developers)

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
git clone https://github.com/ways2read/ebraille-checker-gui.git
cd ebraille-checker-gui
uv sync
```

With pip:

```bash
git clone https://github.com/ways2read/ebraille-checker-gui.git
cd ebraille-checker-gui
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
python -m app
```

## Using the app

1. **Select file…** or **Select folder…**, or **drag and drop** a publication
   onto the window — checking starts automatically. On Windows you can also
   right-click an `.ebrl` → **Validate with eBraille Checker**, or
   **Open with** → eBraille Checker. On macOS, use Finder **Open With** for a
   packaged `.app`.
2. Read the **Result** summary (focus moves there when a check finishes), then
   review **Issues** (filterable).
3. Use **Show full log** only when you need the raw checker output.
4. **Tools → Re-check publication** (`F5`) re-runs the current path after you
   fix issues.
5. **Edit → Clear results** (`Ctrl+Shift+N`) clears the path, verdict, issues,
   and log back to the launch state.
6. **Tools → Check for updates…** offers to download a newer eBraille Checker
   release when one exists.

The **title bar** keeps the app name and appends the verdict (for example
`eBraille Checker — Failed — 3 errors`). The **status bar** shows checker and
Java version information only.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Select file |
| `Ctrl+Shift+O` | Select folder |
| `F5` | Re-check current publication |
| `Ctrl+S` | Save report |
| `Ctrl+Shift+C` | Copy summary |
| `Ctrl+Shift+N` | Clear results |
| `Ctrl+L` | Show/hide full log |
| `Esc` | Exit |
| Enter (in path field) | Check the path currently shown |
| Alt+letter | Button / menu mnemonics (underlined letters) |

### Where data is stored

**Checker** — the app uses the newest available copy in this order:

1. **Updated copy** in application data (after you accept an in-app update)
2. **Bundled copy** shipped with the packaged app (`checker/` next to the executable)
3. **Downloaded copy** on first run (when running from source without a bundle)

| OS | Application data |
|---|---|
| Windows | `%LOCALAPPDATA%\eBrailleCheckerGUI\` |
| macOS | `~/Library/Application Support/eBrailleCheckerGUI/` |
| Linux | `~/.local/share/eBrailleCheckerGUI/` |

Under that folder:

- `checker/` — downloaded or updated checker releases
- `settings.json` — remembered UI language

Packaged builds also include `checker/` beside the executable (or inside the
`.app` bundle on macOS).

## Accessibility

- Native wxPython controls (menus, buttons, list, text fields)
- Logical focus order; the **Result** pane is a large, bold, focusable
  read-only multi-line field so screen readers can tab in and re-read with the
  caret (Up/Down by line, Left/Right by character)
- When a check finishes, focus moves to Result (with a brief leave/refocus if
  it already had focus). The result text is selected on focus so screen readers
  announce it; arrow keys then allow line/character review
- Accessible name includes the verdict text; the window title also appends it
- **Language** menu: English, Français, Español, Deutsch, Português
- Severity and pass/fail are always in text; result colour is only a visual cue

Designed for use with NVDA, JAWS, Narrator, and VoiceOver. Feedback on
accessibility gaps is welcome via GitHub issues.

## Equivalent command line

This app runs the same checker you would invoke manually. For a packaged file:

```bash
java -Xss4m -jar path\to\ebraille-checker.jar --profile ebraille publication.ebrl
```

For an exploded (unpacked) publication folder:

```bash
java -Xss4m -jar path\to\ebraille-checker.jar -mode exp --profile ebraille path\to\folder
```

`-Xss4m` increases the Java thread stack size. Without it, some publications can
trigger `java.lang.StackOverflowError` during RelaxNG validation on smaller JREs.

## Troubleshooting

### “Java was not found”

**Packaged build (Windows):** use the full `dist/eBrailleChecker/` folder (or the
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

Check your network and GitHub availability, then use
**Tools → Download / reinstall checker…**, or download the zip manually from the
[releases page](https://github.com/daisy/ebraille-checker/releases) and extract
`ebraille-checker.jar` into the application data `checker/` folder listed above.

### Extension case (`.eBRL` vs `.ebrl`)

The checker may report that a packaged eBraille file must use the lowercase
extension `.ebrl`. Rename the file if needed.

## Project layout

```text
ebraille-checker-gui/
  app/
    main.py            # wxPython UI
    checker.py         # Run jar, parse JSON results
    updater.py         # GitHub release download / update
    java_util.py       # Locate Java (bundled or PATH)
    models.py          # Verdict and issue models
    i18n.py            # UI translations
    settings.py        # Persisted preferences
    paths.py           # App data and bundle locations
    subprocess_util.py # Quiet subprocess helpers (Windows)
  run.py               # Launcher (incl. SSL cert setup when frozen)
  scripts/
    package.py               # PyInstaller + bundled JRE and checker
    jre_bundle.py            # Download Temurin JRE into runtime/
    checker_bundle.py        # Download eBraille Checker into checker/
    build_installer.ps1      # Windows: package + Inno Setup compile
    build_macos.sh           # macOS: package .app + zip
    build_macos_dmg.sh       # macOS: drag-to-Applications .dmg
    build_macos_release.sh   # macOS: sign + .dmg + notarize
    make_icns.py             # Build .icns (defaults to .ico master)
    macos_release_arch_suffix.inc.sh
  installer/
    eBrailleChecker.iss   # Inno Setup script (Windows installer)
    eBrailleChecker.ico   # App / setup icon (Windows; also Mac .icns master)
    eBrailleChecker.icns  # App / volume icon (macOS)
    icon.png              # Alternate flat artwork (--from-png)
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
**Eclipse Temurin JRE 17** and **eBraille Checker** by default.

```bash
uv sync --extra dev
uv run python scripts/package.py --clean
```

Options:

```bash
uv run python scripts/package.py --no-bundle-java      # smaller build; needs system Java
uv run python scripts/package.py --no-bundle-checker   # checker downloaded on first run
uv run python scripts/package.py --onefile             # not recommended with bundles
```

Output layout (Windows example):

```text
dist/eBrailleChecker/
  eBrailleChecker.exe
  runtime/              # bundled Temurin JRE
    bin/java.exe
  checker/              # bundled eBraille Checker
    bundled_version.txt
    …/ebraille-checker.jar
  … (PyInstaller support files)
```

On Windows, prefer the **Inno Setup** installer (below) for end users. You can
still zip and distribute the entire `dist/eBrailleChecker/` folder if needed —
do not ship only the `.exe`.

On macOS, prefer the **signed and notarized `.dmg`** (below). You can still
distribute the `.app` zip from `scripts/build_macos.sh` if needed.

When a newer checker is released, **Tools → Check for updates…** compares against
the version in use (bundled or previously updated). Accepting an update downloads
the new release into application data; the bundled copy in the install folder is
not modified.

### Windows installer (Inno Setup)

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php). Keep
`MyAppVersion` in `installer/eBrailleChecker.iss` in sync with
`pyproject.toml` / `app/__init__.py`.

One-shot (packages the app, then compiles the setup):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

Or step by step:

```powershell
uv sync --extra dev
uv run python scripts/package.py --clean
# Then compile with Inno Setup Compiler, or:
iscc installer\eBrailleChecker.iss
```

Output: `installer/Output/eBrailleCheckerGUI-<version>-setup.exe`

The installer:

- Ships the full onedir tree (GUI + Temurin JRE 17 + checker) — no system Java
- Supports per-user install (default) or Program Files with elevation
- Adds `.ebrl` shell integration (optional task, on by default):
  **Open with** → eBraille Checker, and context menu **Validate with eBraille
  Checker** — does not change the double-click default
- Offers an optional desktop shortcut and launch-on-finish
- On uninstall, optionally removes `%LOCALAPPDATA%\eBrailleCheckerGUI\`
  (settings and checker updates)

### macOS disk image + notarization

Same pattern as FIDO: build an `.app`, wrap it in a drag-to-Applications
`.dmg`, then **codesign** and **notarize** so Gatekeeper accepts the download.

Prerequisites:

- Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/)
- A **Developer ID Application** certificate in your login keychain
- Notary credentials (one of):
  - Keychain profile: `xcrun notarytool store-credentials "ebraille-notary" …`
  - Or App Store Connect API key (`AuthKey_*.p8` + key id + issuer)

**Signing must use `packaging/macos/entitlements.plist`.** That plist enables
hardened-runtime library loading for PyInstaller **and** the JVM entitlements
(`allow-jit`, `allow-unsigned-executable-memory`) required for the bundled
Temurin JRE. Signing without them makes `runtime/bin/java` crash (`SIGTRAP`),
and the GUI reports Java as missing. `scripts/build_macos_release.sh` applies
this plist automatically.

One-shot release (package → sign → DMG → notarize → staple):

```bash
chmod +x scripts/build_macos_release.sh
EBC_NOTARY_PROFILE=ebraille-notary ./scripts/build_macos_release.sh
# optional explicit version:
EBC_NOTARY_PROFILE=ebraille-notary ./scripts/build_macos_release.sh 0.1.0
```

Outputs (arch suffix is `-AppleSilicon` or `-Intel`):

- `dist/eBrailleChecker_App/eBrailleChecker.app`
- `dist/eBrailleCheckerGUI-macOS-<version>-<arch>.zip`
- `dist/eBrailleCheckerGUI-macos-<version>-<arch>.dmg` (signed + notarized when credentials are set)

Step by step:

```bash
./scripts/build_macos.sh 0.1.0          # .app + zip
./scripts/build_macos_dmg.sh 0.1.0      # drag-install .dmg (unsigned)
```

App icon: `scripts/make_icns.py` builds `installer/eBrailleChecker.icns` from
the Windows `.ico` by default (flatter master). Use `--from-png` for
`installer/icon.png` instead.

Useful environment variables:

| Variable | Meaning |
|----------|---------|
| `EBC_NOTARY_PROFILE` | Keychain profile for `notarytool` |
| `EBC_NOTARY_KEY` / `EBC_NOTARY_KEY_ID` / `EBC_NOTARY_ISSUER` | API-key notary credentials |
| `EBC_APP_SIGN_IDENTITY` | Override Developer ID Application identity |
| `EBC_SKIP_NOTARY=1` | Build and sign only (no notarization) |
| `EBC_SKIP_APP_SIGN=1` | Skip codesign (local smoke builds) |
| `EBC_SKIP_APPLICATION_BUILD=1` | Re-sign / notarize an existing `dist/eBrailleChecker_App/` |

`scripts/package.py` registers the `.ebrl` document type in the `.app`
`Info.plist` with rank **Alternate**, so the app appears under Finder
**Open With** without becoming the default double-click handler. Opening a
file that way launches the GUI and starts a check automatically.

## Test data

Place your own `.ebrl` files or exploded folders under `testdata/` for local
testing. Sample publications are **not** included in the repository.

## Credits

- Conformance checking is performed by
  [eBraille Checker](https://github.com/daisy/ebraille-checker) from the
  [DAISY Consortium](https://daisy.org/), based on EPUBCheck.
- Learn about the [eBraille standard](https://daisy.org/activities/standards/ebraille/)
  and the [eBraille specification](https://daisy.org/s/ebraille/).
- This GUI is a separate front-end project and is not an official DAISY release.

## License

This project (the GUI) is released under the [MIT License](LICENSE).

The eBraille Checker jar downloaded at runtime remains under its own license
(BSD 3-Clause); see the
[upstream repository](https://github.com/daisy/ebraille-checker).
