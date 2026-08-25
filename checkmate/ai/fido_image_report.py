"""Run Fido ``image-report`` and load ``image_report.json`` (no Fido import)."""

from __future__ import annotations

import hashlib
import json
import locale
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fido_launch import fido_cli_command, fido_supports_image_report_cli, find_fido_app
from ..i18n import _, get_language
from ..paths import app_data_dir
from ..subprocess_util import hidden_run_kwargs

logger = logging.getLogger(__name__)

IMAGE_REPORT_JSON = "image_report.json"
HTML_NAME = "alt_text_report.html"
CACHE_FORMAT = 1
_INDEX_NAME = "index.json"
_MAX_CACHED = 12
_PROCESS_SESSION = f"{os.getpid()}-{time.time_ns()}"
CLI_COMMAND = "image-report"
_SUPPORTED_SUFFIXES = {".epub", ".pdf"}
_CLI_PREFIXES = ("[Fido CLI] ", "[Fido CLI] ")

VERDICT_CODES = (
    "unreviewed",
    "ok",
    "likely_ok_with_caveat",
    "needs_attention",
    "uncertain",
)

ProgressCallback = Callable[[str], None]


class FidoImageReportError(RuntimeError):
    """Fido CLI failed or produced an unusable report folder."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class ImageReportImage:
    index: int
    filename: str
    classification: str = ""
    alt_text: str = ""
    status: str = ""
    dimensions: str = ""
    file_size: str = ""
    context: str = ""
    heuristic_flags: list[str] = field(default_factory=list)
    assessment: dict[str, Any] | None = None

    @property
    def alt_stripped(self) -> str:
        return (self.alt_text or "").strip()

    @property
    def is_decorative(self) -> bool:
        return (self.status or "").strip().lower() == "decorative"

    @property
    def has_ai(self) -> bool:
        return isinstance(self.assessment, dict) and bool(self.assessment)

    def verdict_code(self) -> str:
        payload = self.assessment if isinstance(self.assessment, dict) else None
        if not payload:
            return "unreviewed"
        pass_name = str(payload.get("pass") or payload.get("pass_name") or "")
        if pass_name == "error":
            return "unreviewed"
        verdict = str(payload.get("verdict") or "").strip()
        if verdict in VERDICT_CODES and verdict != "unreviewed":
            return verdict
        if verdict:
            return "uncertain"
        return "unreviewed"


@dataclass
class ImageReport:
    folder: Path
    document_name: str = ""
    publication_format: str = "unknown"
    generated: str = ""
    images: list[ImageReportImage] = field(default_factory=list)
    sample: dict[str, Any] | None = None
    synthesis_markdown: str = ""
    qa_markdown: str = ""
    model: str = ""
    schema_version: int = 1
    counts_raw: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.images)

    @property
    def has_ai(self) -> bool:
        return any(im.has_ai for im in self.images)

    def html_path(self) -> Path:
        return self.folder / HTML_NAME

    def json_path(self) -> Path:
        return self.folder / IMAGE_REPORT_JSON

    def counts(self) -> dict[str, int]:
        with_alt = sum(
            1 for im in self.images if not im.is_decorative and im.alt_stripped
        )
        decorative = sum(1 for im in self.images if im.is_decorative)
        missing = sum(
            1 for im in self.images if not im.is_decorative and not im.alt_stripped
        )
        flagged = sum(1 for im in self.images if im.heuristic_flags)
        ai_reviewed = sum(1 for im in self.images if im.has_ai)
        needs = sum(1 for im in self.images if im.verdict_code() == "needs_attention")
        merged = {
            "total": self.total,
            "with_alt": with_alt,
            "decorative": decorative,
            "missing": missing,
            "flagged": flagged,
            "ai_reviewed": ai_reviewed,
            "needs_attention": needs,
        }
        for key, value in self.counts_raw.items():
            if key not in merged and isinstance(value, int):
                merged[key] = value
        return merged

    def verdict_tally(self) -> dict[str, int]:
        data = {code: 0 for code in VERDICT_CODES}
        for im in self.images:
            code = im.verdict_code()
            data[code] = data.get(code, 0) + 1
        return data

    def sample_is_partial(self) -> bool:
        """True when a sniff ran on a percent sample rather than every image."""
        sample = self.sample or {}
        if not sample:
            if self.has_ai and any(not im.has_ai for im in self.images):
                return True
            return False
        mode = str(sample.get("mode") or sample.get("kind") or "").lower()
        if mode in {"all", "assess"}:
            return False
        pct = sample.get("percent")
        if pct is None:
            return any(not im.has_ai for im in self.images) if self.has_ai else False
        try:
            return int(pct) < 100
        except (TypeError, ValueError):
            return False

    sample_is_partial = sample_is_partial

    def qa_context_brief(self, *, max_chars: int = 12000) -> str:
        lines = [
            f"Document: {self.document_name}",
            f"Format: {self.publication_format}",
            f"Images: {self.total}",
            "",
        ]
        synth = (self.synthesis_markdown or "").strip()
        if synth:
            lines.extend(["AI sniff-test summary:", synth, ""])
        lines.append("Per-image inventory:")
        for im in self.images:
            status = (im.status or "").strip() or (
                "decorative"
                if im.is_decorative
                else "has alt"
                if im.alt_stripped
                else "missing alt"
            )
            lines.append(f"#{im.index} {im.filename} ({status})")
            if im.classification:
                lines.append(f"  Classification: {im.classification}")
            if im.alt_stripped:
                lines.append(f"  Alt: {im.alt_stripped}")
            ctx = (im.context or "").strip()
            if ctx:
                if len(ctx) > 400:
                    ctx = ctx[:400].rstrip() + "…"
                lines.append(f"  Surrounding text: {ctx}")
            if im.heuristic_flags:
                lines.append(f"  Heuristic flags: {', '.join(im.heuristic_flags)}")
            if im.has_ai and isinstance(im.assessment, dict):
                verdict = str(im.assessment.get("verdict") or "")
                reason = str(im.assessment.get("reason") or "").strip()
                issues = [
                    str(c)
                    for c in (im.assessment.get("issues") or [])
                    if c and c != "ok"
                ]
                bit = f"  AI: {verdict}"
                if issues:
                    bit += f" [{', '.join(issues)}]"
                if reason:
                    bit += f" — {reason}"
                lines.append(bit)
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            return text[: max_chars - 1].rstrip() + "…"
        return text

    qa_context_brief = qa_context_brief
    qa_context_brief = qa_context_brief


@dataclass
class ImageReportRun:
    folder: Path
    html_path: Path
    json_path: Path
    report: ImageReport
    from_cache: bool = False


def supports_image_report_path(path: Path | str) -> bool:
    text = str(path).strip().strip('"')
    if not text:
        return False
    try:
        p = Path(text)
        return p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES
    except OSError:
        return False


def image_report_cache_dir() -> Path:
    path = app_data_dir() / "image_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_image_report(folder: Path | str) -> ImageReport:
    root = Path(folder).expanduser().resolve()
    json_path = root / IMAGE_REPORT_JSON
    if not json_path.is_file():
        raise FileNotFoundError(f"No {IMAGE_REPORT_JSON} in {root}")
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {IMAGE_REPORT_JSON}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{IMAGE_REPORT_JSON} is not an object")
    images: list[ImageReportImage] = []
    raw_images = payload.get("images") or []
    if not isinstance(raw_images, list):
        raw_images = []
    for i, row in enumerate(raw_images, start=1):
        if not isinstance(row, dict):
            continue
        flags = row.get("heuristics")
        if isinstance(flags, dict):
            flag_list = flags.get("flags") or []
        else:
            flag_list = row.get("heuristic_flags") or []
        if not isinstance(flag_list, list):
            flag_list = []
        assessment = row.get("assessment")
        if assessment is not None and not isinstance(assessment, dict):
            assessment = None
        images.append(
            ImageReportImage(
                index=int(row.get("index") or i),
                filename=str(row.get("filename") or ""),
                classification=str(row.get("classification") or ""),
                alt_text=str(row.get("alt_text") or row.get("alt") or ""),
                status=str(row.get("status") or ""),
                dimensions=str(row.get("dimensions") or ""),
                file_size=str(row.get("file_size") or ""),
                context=str(row.get("context") or ""),
                heuristic_flags=[str(f) for f in flag_list],
                assessment=assessment,
            )
        )
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    counts_raw = {
        str(k): int(v) for k, v in counts.items() if isinstance(v, (int, float))
    }
    return ImageReport(
        folder=root,
        document_name=str(payload.get("document") or payload.get("document_name") or ""),
        publication_format=str(
            payload.get("publication_format")
            or payload.get("publication_format")
            or "unknown"
        ),
        generated=str(payload.get("generated") or ""),
        images=images,
        sample=payload.get("sample") if isinstance(payload.get("sample"), dict) else None,
        synthesis_markdown=str(
            payload.get("synthesis_markdown") or payload.get("synthesis_markdown") or ""
        ),
        qa_markdown=str(payload.get("qa_markdown") or payload.get("qa_markdown") or ""),
        model=str(payload.get("model") or ""),
        schema_version=int(
            payload.get("schema_version") or payload.get("schema_version") or 1
        ),
        counts_raw=counts_raw,
    )


def save_image_report_qa(folder: Path | str, qa_markdown: str) -> bool:
    """Patch ``qa_markdown`` in ``image_report.json`` without rewriting the rest."""
    path = Path(folder) / IMAGE_REPORT_JSON
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Could not read %s to save chat", path, exc_info=True)
        return False
    if not isinstance(payload, dict):
        return False
    payload["qa_markdown"] = qa_markdown or ""
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.debug("Could not write chat to %s", path, exc_info=True)
        return False
    return True


def report_folder_is_complete(folder: Path) -> bool:
    return (folder / IMAGE_REPORT_JSON).is_file() and (folder / HTML_NAME).is_file()


def source_path_from_folder(folder: Path | str) -> Path | None:
    """Return the publication path recorded in a cached report folder."""
    path = Path(folder) / _INDEX_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("resolved") or data.get("path") or ""
    if not raw:
        return None
    candidate = Path(str(raw))
    return candidate if candidate.is_file() else None


def _fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    st = resolved.stat()
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    return {
        "resolved": str(resolved),
        "mtime_ns": mtime_ns,
        "size": int(st.st_size),
        "format": CACHE_FORMAT,
        "language": _ui_language_code(),
    }


def _cache_key(fp: dict[str, Any]) -> str:
    raw = (
        f"{fp['resolved']}|{fp['mtime_ns']}|{fp['size']}|{fp['format']}"
        f"|{fp.get('language') or ''}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _folder_matches_cache_key(folder: Path, key: str) -> bool:
    name = folder.name
    return name == key or name.startswith(f"{key}_")


def _new_output_folder(fp: dict[str, Any]) -> Path:
    """Empty dest for one Fido CLI run.

    Older Fido builds write into ``--output`` without clearing it. A unique
    folder per run means CheckMate never needs a newer Fido for a clean report.
    """
    folder = image_report_cache_dir() / f"{_cache_key(fp)}_{uuid.uuid4().hex[:10]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _latest_cached_run(fp: dict[str, Any]) -> ImageReportRun | None:
    key = _cache_key(fp)
    root = image_report_cache_dir()
    try:
        dirs = [p for p in root.iterdir() if p.is_dir() and _folder_matches_cache_key(p, key)]
    except OSError:
        return None
    hits: list[tuple[float, ImageReportRun]] = []
    for folder in dirs:
        cached = _cached_run(folder, fp)
        if cached is None:
            continue
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = 0.0
        hits.append((mtime, cached))
    if not hits:
        return None
    hits.sort(key=lambda item: item[0])
    return hits[-1][1]


def _write_manifest(folder: Path, fp: dict[str, Any]) -> None:
    payload = {**fp, "saved_at": time.time(), "session_id": _PROCESS_SESSION}
    (folder / _INDEX_NAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _manifest_matches(folder: Path, fp: dict[str, Any]) -> bool:
    path = folder / _INDEX_NAME
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return (
        data.get("resolved") == fp["resolved"]
        and int(data.get("mtime_ns") or 0) == fp["mtime_ns"]
        and int(data.get("size") or 0) == fp["size"]
        and int(data.get("format") or 0) == CACHE_FORMAT
        and str(data.get("language") or "") == str(fp.get("language") or "")
        and str(data.get("session_id") or "") == _PROCESS_SESSION
    )


def _prune_cache(keep: Path) -> None:
    root = image_report_cache_dir()
    try:
        dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    if len(dirs) <= _MAX_CACHED:
        return
    dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    extra = len(dirs) - _MAX_CACHED
    for folder in dirs:
        if extra <= 0:
            break
        try:
            if folder.resolve() == keep.resolve():
                continue
            shutil.rmtree(folder, ignore_errors=True)
            extra -= 1
        except OSError:
            pass


def _strip_cli_prefix(line: str) -> str:
    text = line.strip()
    for prefix in _CLI_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _vision_progress_line_starters() -> tuple[str, ...]:
    """Labels that start a new Fido vision-progress row (English + current UI)."""
    starters = [
        "Likely OK:",
        "OK with caveat:",
        "Est. time remaining:",
        f"{_('Likely OK')}:",
        f"{_('OK with caveat')}:",
        _("Est. time remaining: {eta}").split("{", 1)[0].strip(),
    ]
    return tuple(dict.fromkeys(item for item in starters if item))


def _unwrap_vision_speech_progress(text: str) -> str:
    """Turn Fido's spoken one-liner back into the four dialog lines."""
    if "\n" in text:
        return text
    if not any(token in text for token in _vision_progress_line_starters()):
        return text
    out = text
    for marker in _vision_progress_line_starters():
        idx = 0
        while True:
            found = out.find(marker, idx)
            if found <= 0:
                break
            prefix = out[:found].rstrip(" .")
            out = f"{prefix}\n{out[found:]}"
            idx = len(prefix) + 1 + len(marker)
    return out


def sanitize_cli_progress(message: str) -> str:
    """Keep Fido status lines; drop CRs, NULs, and decode-replacement glyphs."""
    raw = (message or "").replace("\x00", "").replace("\r", "\n")
    raw = raw.replace("\u2028", "\n")
    raw = (
        raw.replace("\ufffd", "")
        .replace("\u2026", "...")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    raw = _unwrap_vision_speech_progress(raw)
    parts: list[str] = []
    for line in raw.split("\n"):
        cleaned = "".join(ch if ch >= " " else " " for ch in line).rstrip()
        parts.append(cleaned)
    while parts and not parts[0].strip():
        parts.pop(0)
    while parts and not parts[-1].strip():
        parts.pop()
    return "\n".join(parts)


def progress_speech_text(message: str) -> str:
    """Spoken form of a padded ProgressDialog body."""
    lines = [part.strip() for part in sanitize_cli_progress(message).split("\n") if part.strip()]
    return ". ".join(lines)


def _decode_cli_line(raw: bytes) -> str:
    data = raw.replace(b"\x00", b"")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        enc = locale.getpreferredencoding(False) or "cp1252"
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _unsupported_cli_message() -> str:
    return _(
        "This copy of Fido cannot build image reports. Update Fido, then try again."
    )


def _looks_like_unsupported_cli(detail: str) -> bool:
    text = (detail or "").lower()
    hints = (
        "already running",
        "unrecognized arguments",
        "invalid choice",
        "unknown command",
        "invalid command",
    )
    return any(hint in text for hint in hints)


def _message_for_exit_code(code: int, detail: str) -> str:
    detail = (detail or "").strip()
    mapping = {
        2: _("Fido could not read that file. Image reports need a packaged EPUB or PDF."),
        3: _("Fido could not create the report folder."),
        4: _("Fido could not start AI. Check API keys or the unlock code in Fido."),
        5: _("Fido could not open the publication."),
        6: _("Fido could not build the image report."),
        7: _("The AI Image Sniff Test failed. Check the Fido model and try again."),
    }
    base = mapping.get(code, _("Fido could not build the image report."))
    if detail and detail.lower() not in base.lower():
        return f"{base}\n\n{detail}"
    return base


def _ui_language_code() -> str:
    return (get_language() or "en").strip() or "en"


def _build_cli_argv(
    *,
    input_path: Path,
    output_dir: Path,
    assess: bool,
    percent: int | None,
) -> list[str]:
    prefix = fido_cli_command()
    if not prefix:
        raise FidoImageReportError(
            _("Image reports need Fido. Install Fido, then try again.")
        )
    argv = [
        *prefix,
        CLI_COMMAND,
        "--input",
        str(input_path),
        "--output",
        str(output_dir),
        "--language",
        _ui_language_code(),
    ]
    if percent is not None:
        argv.extend(["--percent", str(int(percent))])
    elif assess:
        argv.append("--assess")
    return argv


def _run_fido_process(
    argv: list[str],
    *,
    cancel_event: threading.Event | None,
    progress: ProgressCallback | None,
) -> tuple[int, str]:
    kwargs = hidden_run_kwargs()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    logger.info("Running Fido image-report: %s", argv)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
            **kwargs,
        )
    except OSError as exc:
        raise FidoImageReportError(
            _("Could not start Fido:\n{error}").format(error=exc)
        ) from exc

    chunks: list[str] = []
    assert proc.stdout is not None

    def _reader() -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                decoded = _decode_cli_line(raw)
                chunks.append(decoded)
                line = sanitize_cli_progress(_strip_cli_prefix(decoded))
                if line and progress is not None:
                    try:
                        progress(line)
                    except Exception:
                        pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    pass
                thread.join(timeout=2)
                raise FidoImageReportError(_("The image report was cancelled."))
            time.sleep(0.15)
    finally:
        thread.join(timeout=30)
    text = "".join(chunks).strip()
    last = ""
    for line in reversed(text.splitlines()):
        stripped = sanitize_cli_progress(_strip_cli_prefix(line))
        if stripped:
            last = stripped
            break
    return int(proc.returncode if proc.returncode is not None else -1), last or text


def _cached_run(folder: Path, fp: dict[str, Any]) -> ImageReportRun | None:
    if not (
        folder.is_dir()
        and report_folder_is_complete(folder)
        and _manifest_matches(folder, fp)
    ):
        return None
    try:
        report = load_image_report(folder)
    except (OSError, ValueError, FileNotFoundError):
        return None
    return ImageReportRun(
        folder=folder,
        html_path=report.html_path(),
        json_path=report.json_path(),
        report=report,
        from_cache=True,
    )


def peek_cached_image_report(input_path: Path | str) -> ImageReportRun | None:
    """Return this CheckMate session's cached report, or None."""
    src = Path(str(input_path).strip().strip('"')).expanduser()
    try:
        if not src.is_file() or src.suffix.lower() not in _SUPPORTED_SUFFIXES:
            return None
        fp = _fingerprint(src.resolve())
    except OSError:
        return None
    return _latest_cached_run(fp)


def run_fido_image_report(
    input_path: Path | str,
    *,
    assess: bool = False,
    percent: int | None = None,
    dest: Path | None = None,
    use_cache: bool = True,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> ImageReportRun:
    """Build (or reuse) a Fido image-report folder for *input_path*."""
    src = Path(str(input_path).strip().strip('"')).expanduser()
    if not src.is_file():
        raise FidoImageReportError(_("File not found: {path}").format(path=src))
    if src.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise FidoImageReportError(
            _("Image reports need a packaged EPUB or PDF.")
        )
    if find_fido_app() is None:
        raise FidoImageReportError(
            _("Image reports need Fido. Install Fido, then try again.")
        )
    if not fido_supports_image_report_cli():
        raise FidoImageReportError(_unsupported_cli_message())

    src = src.resolve()
    fp = _fingerprint(src)
    do_assess = bool(assess or percent is not None)
    if dest is not None:
        folder = Path(dest)
        folder.mkdir(parents=True, exist_ok=True)
    else:
        if use_cache and not do_assess:
            cached = _latest_cached_run(fp)
            if cached is not None:
                return cached
        # Always a new empty --output. Reusing a folder required Fido to
        # wipe leftovers; frozen installs may not. AI rebuilds also get a
        # fresh dest so inventory + sniff cannot mix two publications.
        folder = _new_output_folder(fp)

    if progress is not None:
        progress(_("Asking Fido to build the image report…"))

    argv = _build_cli_argv(
        input_path=src, output_dir=folder, assess=do_assess, percent=percent
    )
    code, detail = _run_fido_process(
        argv, cancel_event=cancel_event, progress=progress
    )
    if code != 0:
        if _looks_like_unsupported_cli(detail):
            raise FidoImageReportError(
                _unsupported_cli_message(), exit_code=code
            )
        raise FidoImageReportError(
            _message_for_exit_code(code, detail), exit_code=code
        )
    if not report_folder_is_complete(folder):
        raise FidoImageReportError(
            _("Fido finished but did not write image_report.json or the HTML report.")
        )
    _write_manifest(folder, fp)
    _prune_cache(folder)
    report = load_image_report(folder)
    return ImageReportRun(
        folder=folder,
        html_path=report.html_path(),
        json_path=report.json_path(),
        report=report,
        from_cache=False,
    )


def image_report_mode_choices() -> list[tuple[str, bool]]:
    """``(label, with_ai)`` rows for the Images-button chooser."""
    return [
        (_("Image report"), False),
        (_("Image report with AI analysis"), True),
    ]


def image_report_ai_sample_choices() -> list[tuple[str, int | None]]:
    """How many images to send when the user chose AI analysis."""
    return [
        (_("About 25% of images"), 25),
        (_("About 50% of images"), 50),
        (_("All images"), None),
    ]


def sample_percent_choices(total: int) -> list[tuple[str, int | None]]:
    """Return (label, percent_or_None_for_all) rows for the sniff picker."""
    if total <= 0:
        return []
    if total <= 20:
        return [(_("All {n} images").format(n=total), None)]
    rows: list[tuple[str, int | None]] = [
        (_("About 25% ({n} images)").format(n=max(1, round(total * 0.25))), 25),
        (_("About 50% ({n} images)").format(n=max(1, round(total * 0.5))), 50),
        (_("All {n} images").format(n=total), None),
    ]
    if total > 40:
        rows.insert(
            0,
            (_("About 10% ({n} images)").format(n=max(1, round(total * 0.10))), 10),
        )
    return rows


PROGRESS_LINE_COUNT = 5
PROGRESS_LINE_MAX_CHARS = 72


def _fit_progress_line(line: str, *, width: int = PROGRESS_LINE_MAX_CHARS) -> str:
    text = (line or "").rstrip()
    if not text.strip():
        return " "
    if len(text) <= width:
        return text
    return text[: max(1, width - 3)].rstrip() + "..."


def pad_progress_message(message: str, *, lines: int = PROGRESS_LINE_COUNT) -> str:
    """Keep wx.ProgressDialog height and width stable across status updates."""
    reserved = " "
    raw = sanitize_cli_progress(message)
    parts = [
        _fit_progress_line(segment) if segment.strip() else reserved
        for segment in raw.split("\n")
    ]
    if not parts:
        parts.append(reserved)
    while len(parts) < lines:
        parts.append(reserved)
    return "\n".join(parts[:lines])


def make_progress_dialog(
    title: str,
    message: str,
    parent: object | None,
    *,
    maximum: int = 100,
    style: int | None = None,
):
    """Create a progress dialog that can show reserved multiline status.

    Native Windows Task Dialog draws extra newlines as replacement glyphs, so
    this always uses GenericProgressDialog when wx provides it.
    """
    import wx

    cls = getattr(wx, "GenericProgressDialog", None) or wx.ProgressDialog
    if style is None:
        style = wx.PD_APP_MODAL | wx.PD_CAN_ABORT
    return cls(
        title,
        pad_progress_message(message),
        maximum=maximum,
        parent=parent,
        style=style,
    )


def verdict_tally_label(code: str) -> str:
    return {
        "unreviewed": _("Not AI-reviewed"),
        "ok": _("Likely OK"),
        "likely_ok_with_caveat": _("OK with caveat"),
        "needs_attention": _("Needs attention"),
        "uncertain": _("Uncertain"),
    }.get(code, code)


def verdict_tally_short_label(code: str) -> str:
    return {
        "unreviewed": _("Not reviewed"),
        "ok": _("OK"),
        "likely_ok_with_caveat": _("Caveat"),
        "needs_attention": _("Needs"),
        "uncertain": _("Uncertain"),
    }.get(code, verdict_tally_label(code))


def verdict_tally_pill_text(
    *,
    full: str,
    short: str,
    count: int,
    max_width: int,
    measure: Callable[[str], int],
) -> str:
    count_s = str(int(count))
    candidates = [f"{full} {count_s}"]
    if short and short != full:
        candidates.append(f"{short} {count_s}")
    candidates.append(count_s)
    for text in candidates:
        try:
            width = int(measure(text) or 0)
        except Exception:
            width = int(max_width) + 1
        if width <= int(max_width):
            return text
    return count_s


def format_verdict_tally_spoken(counts: dict[str, int] | None) -> str:
    data = counts or {}
    parts = []
    for code in VERDICT_CODES:
        n = int(data.get(code) or 0)
        if code == "uncertain" and n <= 0:
            continue
        parts.append(f"{n} {verdict_tally_label(code)}")
    return _("Assessment summary: {details}").format(details=", ".join(parts))
