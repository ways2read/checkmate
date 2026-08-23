"""AI vision assessment of alt text from a Fido export."""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from ..i18n import _, get_language, language_display_name
from .alt_export import (
    AltExport,
    AltExportImage,
    infer_publication_format,
    load_alt_export,
    publication_format_label,
)
from .alt_heuristics import (
    FLAG_CLASS_DECORATIVE_MISMATCH,
    FLAG_DECORATIVE_WITH_ALT,
    FLAG_DUPLICATE_ALT,
    FLAG_EMPTY_HAS_ALT,
    FLAG_FILENAME_AS_ALT,
    FLAG_JOINED_IMAGES,
    FLAG_LOW_RESOLUTION,
    FLAG_MISSING_ALT,
    FLAG_PLACEHOLDER_ALT,
    HeuristicReport,
    run_heuristics,
    summarize_heuristics,
)
from .alt_sample import (
    DEFAULT_SAMPLE_PERCENT,
    SamplePlan,
    build_sample_plan,
    describe_sample_plan,
    merge_sample_plans,
)
from .explain import ExplainResult
from .litellm_client import (
    DEFAULT_EXPLAIN_MAX_TOKENS,
    ensure_credentials_ready,
    litellm_available,
)
from .session import ExplainSession, ProviderError

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]

_MAX_EDGE_DEFAULT = 1024
_MAX_IMAGE_MB_DEFAULT = 5.0
_JPEG_QUALITY_DEFAULT = 80
_VISION_MAX_TOKENS = 2048
_SYNTH_MAX_TOKENS = DEFAULT_EXPLAIN_MAX_TOKENS
_DEFAULT_VISION_WORKERS = 8
_MAX_VISION_WORKERS = 16
_FATAL_VISION_KEYS = frozenset({"no_key", "no_model", "network", "timeout"})
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BACKOFF_INITIAL_S = 2.0
_RATE_LIMIT_BACKOFF_MAX_S = 30.0
_VISION_PROGRESS_LINE_COUNT = 5
_VISION_PROGRESS_RESERVED_LINE = " "
# PyMuPDF is not thread-safe; parallel vision workers serialize encode.
_FITZ_ENCODE_LOCK = threading.Lock()


def _vision_image_url_part(data_uri: str, model: str | None) -> dict[str, Any]:
    """Build an OpenAI-style image part; omit ``detail`` for Gemini.

    LiteLLM maps ``detail`` to Gemini ``mediaResolution`` nested inside
    ``inline_data`` in some versions, which the Gemini API rejects (400).
    """
    image_url: dict[str, Any] = {"url": data_uri}
    m = (model or "").lower()
    if "gemini" not in m:
        image_url["detail"] = "low"
    return {"type": "image_url", "image_url": image_url}


def _is_rate_limit_error(error_key: str | None, detail: str | None) -> bool:
    """True when the provider is asking us to slow down (retryable)."""
    d = f"{error_key or ''} {detail or ''}".lower()
    return any(
        marker in d
        for marker in (
            "429",
            "rate limit",
            "rate_limit",
            "ratelimit",
            "resource_exhausted",
            "resource exhausted",
            "too many requests",
            "quota exceeded",
            "exceeded your current quota",
        )
    )


def _fatal_vision_provider_error(error_key: str | None, detail: str | None) -> bool:
    """True when further per-image vision calls are unlikely to succeed."""
    if (error_key or "") != "provider_error":
        return False
    d = (detail or "").lower()
    return any(
        marker in d
        for marker in (
            "mediaresolution",
            "invalid_argument",
            "badrequesterror",
            "unknown name",
            "invalid json payload",
        )
    )


def vision_image_limits() -> tuple[int, int, int]:
    """Return ``(max_edge, max_bytes, jpeg_quality)`` from FIDO when available.

    Mirrors FIDO ``image_resize_pixels`` / ``image_compression_mb``.
    """
    max_edge = _MAX_EDGE_DEFAULT
    max_mb = _MAX_IMAGE_MB_DEFAULT
    try:
        from ..fido_settings import read_user_settings

        settings = read_user_settings()
        raw_edge = settings.get("image_resize_pixels", max_edge)
        raw_mb = settings.get("image_compression_mb", max_mb)
        try:
            max_edge = int(raw_edge)
        except (TypeError, ValueError):
            max_edge = _MAX_EDGE_DEFAULT
        try:
            max_mb = float(raw_mb)
        except (TypeError, ValueError):
            max_mb = _MAX_IMAGE_MB_DEFAULT
    except Exception:
        logger.debug("Could not read FIDO image compression settings", exc_info=True)
    max_edge = max(64, min(4096, max_edge))
    max_mb = max(0.25, min(50.0, max_mb))
    max_bytes = int(max_mb * 1024 * 1024)
    return max_edge, max_bytes, _JPEG_QUALITY_DEFAULT


def vision_parallel_workers(
    model: str | None = None, *, requested: int | None = None
) -> int:
    """How many concurrent vision calls to start with.

    Override with CheckMate ``ai_alt_assess_workers`` (1–16) or *requested*.
    Default is 8; a 429 / quota error halves the in-flight count for later waves.
    """
    if requested is not None:
        try:
            return max(1, min(int(requested), _MAX_VISION_WORKERS))
        except (TypeError, ValueError):
            pass
    try:
        from ..settings import read_settings

        raw = read_settings().get("ai_alt_assess_workers")
        if raw not in (None, ""):
            return max(1, min(int(raw), _MAX_VISION_WORKERS))
    except Exception:
        logger.debug("Could not read AI Image Sniff Test worker setting", exc_info=True)
    return _DEFAULT_VISION_WORKERS


def _progress_dialog_lead_nl() -> bool:
    """Whether to prefix a blank line for native Windows Task Dialog sizing.

    GenericProgressDialog (macOS, GTK, and CheckMate's dark-mode Windows
    patch) shows a leading newline as an empty first row.
    """
    if sys.platform != "win32":
        return False
    try:
        import wx

        generic = getattr(wx, "GenericProgressDialog", None)
        return generic is None or wx.ProgressDialog is not generic
    except Exception:
        return True


def format_vision_progress_dialog(message: str) -> str:
    """Fixed-height ProgressDialog body.

    wx sizes the dialog from the first message. Padding to a constant line
    count keeps later updates from growing or collapsing the window. A
    leading newline is Windows Task Dialog only.
    """
    raw = (message or "").replace("\r\n", "\n").lstrip("\n")
    lines: list[str] = []
    for segment in raw.split("\n"):
        lines.append(segment if segment.strip() else _VISION_PROGRESS_RESERVED_LINE)
    if not lines:
        lines.append(_VISION_PROGRESS_RESERVED_LINE)
    while len(lines) < _VISION_PROGRESS_LINE_COUNT:
        lines.append(_VISION_PROGRESS_RESERVED_LINE)
    body = "\n".join(lines[:_VISION_PROGRESS_LINE_COUNT])
    if _progress_dialog_lead_nl():
        return "\n" + body
    return body


def _humanize_eta_seconds(seconds: float) -> str:
    """Short remaining-time phrase for the progress dialog."""
    s = max(0.0, float(seconds))
    if s < 50:
        return _("less than a minute")
    if s < 90:
        return _("about a minute")
    minutes = int(round(s / 60.0))
    if minutes < 60:
        return _("about {n} min").format(n=minutes)
    hours = minutes // 60
    rem = minutes % 60
    if rem == 0:
        return _("about {n} h").format(n=hours)
    return _("about {hours} h {minutes} min").format(hours=hours, minutes=rem)


def vision_progress_message(
    *,
    done: int,
    total: int,
    elapsed_s: float,
    verdicts: dict[str, int] | None = None,
    note: str = "",
) -> str:
    """Reserved multiline status for the vision ProgressDialog."""
    if done <= 0:
        line1 = _("Assessing {total} images…").format(total=total)
    else:
        line1 = _("Finished {done} of {total} images.").format(done=done, total=total)
    counts = verdicts or {}
    # Two short lines so GenericProgressDialog (macOS) does not widen.
    line2 = (
        f"{_('Likely OK')}: {int(counts.get('ok', 0))}    "
        f"{_('Needs attention')}: {int(counts.get('needs_attention', 0))}"
    )
    line3 = (
        f"{_('OK with caveat')}: {int(counts.get('likely_ok_with_caveat', 0))}    "
        f"{_('Uncertain')}: {int(counts.get('uncertain', 0))}"
    )
    if done > 0 and elapsed_s >= 0.5 and done < total:
        remaining = max(0, total - done)
        eta = remaining / (done / elapsed_s)
        line4 = _("Est. time remaining: {eta}").format(eta=_humanize_eta_seconds(eta))
    elif done < total:
        line4 = _("Est. time remaining: calculating…")
    else:
        line4 = ""
    return format_vision_progress_dialog(
        "\n".join((line1, line2, line3, line4, note or ""))
    )


def _mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
    }.get(suffix, "image/jpeg")


_VERDICTS = frozenset(
    {"ok", "needs_attention", "likely_ok_with_caveat", "uncertain"}
)
_CONFIDENCE = frozenset({"low", "medium", "high"})
_STATUSES = frozenset({"has_alt", "decorative", "missing"})
_QUALITY = frozenset({"good", "weak", "bad", "n_a", "uncertain"})
_ISSUE_VOCAB = frozenset(
    {
        "placeholder_alt",
        "filename_as_alt",
        "inaccurate_alt",
        "too_vague",
        "too_verbose",
        "missing_text_in_image",
        "likely_content_marked_decorative",
        "likely_decorative_with_alt",
        "duplicate_alt",
        "missing_alt",
        "empty_has_alt",
        # Accessible format / presentation review (not just alt wording)
        "image_of_table",
        "image_of_math",
        "likely_wrong_orientation",
        "low_resolution",
        "joined_images",
        "repeats_surrounding_text",
        "wrong_language",
        "spelling_or_grammar",
        "ok",
    }
)

# Models that are clearly text-only (conservative deny list).
_NON_VISION_HINTS = (
    "gpt-3.5",
    "o1-mini",
    "o3-mini",
    "deepseek-chat",
    "deepseek-reasoner",
    "codestral",
    "mistral-small",
    "groq/",
)


@dataclass
class AltImageAssessment:
    index: int
    filename: str
    verdict: str = "uncertain"
    confidence: str = "low"
    status_ok: bool = True
    recommended_status: str = "has_alt"
    descriptiveness: str = "uncertain"
    accuracy: str = "uncertain"
    usefulness: str = "uncertain"
    issues: list[str] = field(default_factory=list)
    reason: str = ""
    teaching_note: str = ""
    suggested_alt: str | None = None  # always null in v1
    pass_name: str = "vision"  # "vision" | "heuristic" | "error"
    error: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pass"] = d.pop("pass_name")
        return d


@dataclass
class AltAssessResult:
    ok: bool
    error_key: str | None = None
    text: str = ""  # synthesis markdown
    detail: str = ""
    session: ExplainSession | None = None
    export: AltExport | None = None
    heuristics: HeuristicReport | None = None
    sample: SamplePlan | None = None
    assessments: list[AltImageAssessment] = field(default_factory=list)
    json_path: Path | None = None
    model: str = ""


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _status(cb: StatusCallback | None, message: str) -> None:
    if cb is not None:
        try:
            cb(format_vision_progress_dialog(message))
        except Exception:
            logger.debug("AI status callback failed", exc_info=True)


def _language_name() -> str:
    return language_display_name()


def model_likely_supports_vision(model: str) -> bool:
    """Heuristic gate: reject known text-only model ids."""
    m = (model or "").strip().lower()
    if not m:
        return False
    for hint in _NON_VISION_HINTS:
        if hint in m:
            return False
    return True


def _encode_image_with_fitz(
    path: Path, *, edge: int, quality: int, byte_cap: int
) -> tuple[str, str]:
    import fitz  # type: ignore

    pix = fitz.Pixmap(str(path))
    if pix.n - pix.alpha >= 4:  # CMYK or similar → RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)  # drop alpha for JPEG

    w, h = pix.width, pix.height
    longest = max(w, h) or 1
    if longest > edge:
        scale = edge / float(longest)
        pix = fitz.Pixmap(
            pix,
            max(1, int(round(w * scale))),
            max(1, int(round(h * scale))),
        )

    q = quality
    data = pix.tobytes("jpeg", jpg_quality=q)
    # Shrink / lower quality until under the FIDO MB cap.
    while len(data) > byte_cap and (pix.width > 64 or q > 40):
        if len(data) > byte_cap and q > 40:
            q = max(40, q - 10)
            data = pix.tobytes("jpeg", jpg_quality=q)
            continue
        shrink = min(0.85, (byte_cap / max(len(data), 1)) ** 0.5)
        pix = fitz.Pixmap(
            pix,
            max(64, int(pix.width * shrink)),
            max(64, int(pix.height * shrink)),
        )
        data = pix.tobytes("jpeg", jpg_quality=q)

    b64 = base64.standard_b64encode(data).decode("ascii")
    logger.debug(
        "Encoded %s for vision: %sx%s jpeg q=%s %s bytes (cap %s)",
        path.name,
        pix.width,
        pix.height,
        q,
        len(data),
        byte_cap,
    )
    return f"data:image/jpeg;base64,{b64}", "image/jpeg"


def encode_image_for_vision(
    path: Path,
    *,
    max_edge: int | None = None,
    max_bytes: int | None = None,
    jpeg_quality: int | None = None,
) -> tuple[str, str]:
    """Return ``(data_uri, mime)`` suitable for LiteLLM vision.

    Resizes to FIDO ``image_resize_pixels`` (default 1024) and compresses to
    JPEG under ``image_compression_mb`` (default 5 MB) when pymupdf is available.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))

    lim_edge, lim_bytes, lim_quality = vision_image_limits()
    edge = max_edge if max_edge is not None else lim_edge
    byte_cap = max_bytes if max_bytes is not None else lim_bytes
    quality = jpeg_quality if jpeg_quality is not None else lim_quality
    quality = max(40, min(95, int(quality)))

    # Try pymupdf (already a CheckMate dependency) for resize + JPEG.
    # MuPDF is not thread-safe; hold the lock for the whole pixmap lifetime.
    try:
        with _FITZ_ENCODE_LOCK:
            return _encode_image_with_fitz(
                path, edge=edge, quality=quality, byte_cap=byte_cap
            )
    except Exception:
        logger.debug("pymupdf image encode failed; using raw file", exc_info=True)

    raw = path.read_bytes()
    if len(raw) > byte_cap:
        logger.warning(
            "Image %s is large (%s bytes); sending without resize "
            "(pymupdf unavailable)",
            path.name,
            len(raw),
        )
    mime = _mime_for(path)
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}", mime


def _section_headings() -> tuple[str, str, str, str, str]:
    return (
        _("Overall assessment"),
        _("Main themes"),
        _("Priority queue"),
        _("What good alt text means here"),
        _("Caveats"),
    )


def _publication_format_rules(publication_format: str) -> str:
    """Coaching that depends on PDF vs EPUB vs eBraille, etc."""
    key = infer_publication_format(explicit=publication_format)
    label = publication_format_label(key)
    common = (
        f"Publication format: {label} (code: {key}).\n"
        "Tailor teaching_note and remediation to THIS format. Do not recommend "
        "techniques that only exist in a different format."
    )
    if key == "pdf":
        return f"""{common}
- PDF has no extended-description feature (no aria-details, details/summary,
  longdesc, or hidden long-description container). Accessible name is the
  figure /Alt. If more than a short alt is needed, that content belongs in
  the visible body text of the PDF, not a hidden extended description.
- Math: keep the equation image if needed and tag/associate it with MathML
  (PDF 2.0 / tagged PDF). Do not recommend EPUB MathML, Word OMML, or
  replacing the figure with HTML.
- Tables: use a real tagged PDF table, not a screenshot of a table.
- Do not recommend MathJax or MathML alttext / alt-text attributes."""
    if key in {"epub", "ebrl"}:
        host = "eBraille" if key == "ebrl" else "EPUB"
        return f"""{common}
- {host} supports extended descriptions in addition to short alt. Keep alt
  concise (the img/figure accessible name). For complex images, recommend an
  extended description in the {host} — for example a details/summary block,
  a following description, or aria-details pointing to a longer description.
  Do not dump the extended description into alt.
- Math: encode as MathML in the {host} package. Do not recommend PDF
  associated-file MathML or Word OMML as the {host} fix.
- Tables: use HTML/{host} table markup, not a picture of a table.
- Do not recommend MathJax or MathML alttext / alt-text attributes."""
    if key == "docx":
        return f"""{common}
- Word alt text is the short description on the picture. Extended
  descriptions belong in the document body immediately after the image, not
  only in the alt-text field.
- Math: use a real Word equation (OMML). Do not recommend PDF associated
  MathML or EPUB aria-details as the Word fix.
- Tables: use a real Word table, not a picture of a table.
- Do not recommend MathJax or MathML alttext / alt-text attributes."""
    if key == "html":
        common = (
            f"Host format: HTML web page (code: html).\n"
            "This is a web page, not a packaged publication. "
            "Tailor teaching_note and remediation to HTML. Do not recommend "
            "techniques that only exist in EPUB, PDF, or Word."
        )
        return f"""{common}
- HTML supports a short alt plus an extended description (figcaption,
  a following description, or aria-details / aria-describedby). Do not dump
  a long description into alt.
- Math: MathML in the HTML. Not PDF associated files or Word OMML.
- Tables: real HTML tables, not screenshots.
- Do not recommend MathJax or MathML alttext / alt-text attributes."""
    return f"""{common}
- Format is not known. Give format-neutral advice: do not assume PDF
  tagging, EPUB extended descriptions, or Word OMML.
- Math encodings by format: EPUB/HTML/eBraille → MathML; Word → OMML; PDF →
  equation image tagged/associated with MathML. Never MathJax or MathML
  alttext as the accessibility solution.
- Tables should be real table markup in the host format, not a screenshot.
- Extended descriptions exist for EPUB/HTML/eBraille and as body content
  after a Word image; PDF and PowerPoint do not have that feature."""


def build_vision_system_prompt(publication_format: str = "") -> str:
    lang = _language_name()
    lang_code = get_language()
    format_rules = _publication_format_rules(publication_format)
    key = infer_publication_format(explicit=publication_format)
    if key == "html":
        host_intro = (
            "You review one image from a web page together with its\n"
            "declared alt text status, any existing alt text, and optional surrounding\n"
            "text from the page."
        )
    else:
        host_intro = (
            "You review one image from a publication together with its\n"
            "declared alt text status, any existing alt text, and optional surrounding page\n"
            "text from the publication."
        )
    return f"""You are an accessibility publishing assistant inside CheckMate.
{host_intro}

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write "reason" and "teaching_note" in {lang}.
- JSON keys and enum values stay in English exactly as specified.

Respond with ONLY a single JSON object (no markdown fences, no commentary) using
these keys:
- verdict: one of ok | needs_attention | likely_ok_with_caveat | uncertain
- confidence: one of low | medium | high
- status_ok: boolean (is the current decorative/has-alt/missing choice reasonable?)
- recommended_status: one of has_alt | decorative | missing
- descriptiveness: one of good | weak | bad | n_a | uncertain
  (n_a when decorative and empty alt is appropriate)
- accuracy: one of good | weak | bad | n_a | uncertain
- usefulness: one of good | weak | bad | n_a | uncertain
- issues: array of zero or more of:
  placeholder_alt, filename_as_alt, inaccurate_alt, too_vague, too_verbose,
  missing_text_in_image, likely_content_marked_decorative,
  likely_decorative_with_alt, duplicate_alt, missing_alt, empty_has_alt,
  image_of_table, image_of_math, likely_wrong_orientation, low_resolution,
  joined_images, repeats_surrounding_text, wrong_language,
  spelling_or_grammar, ok
- reason: one short sentence explaining the verdict
- teaching_note: one short coaching sentence for a non-expert reviewer
- suggested_alt: always null in this version

Rules:
- Prefer under-flagging on borderline decorative vs content decisions.
- When surrounding page text is provided, judge whether the alt fits THIS
  document: what readers already get from nearby prose, what the image adds,
  and whether decorative status is plausible in context. Prefer document fit
  over a generic full caption; shorter can be better.
- Do not invent book/chapter context beyond the image and any surrounding text
  supplied in the user message.
- Filename-like or generic alts ("image", "photo 1", "file001.jpg") are bad.
- Content photographs, step composites, and labeled product packaging usually
  need descriptive alt, not decorative.
- Purely decorative borders/spacers/flourishes may correctly be decorative.
- Accessible format (flag even when alt wording is otherwise fine):
  - image_of_table: the picture is primarily a data table (rows/columns of
    text or numbers). Accessible publications should use real table markup,
    not a screenshot of a table. Prefer needs_attention; say so in
    teaching_note.
  - image_of_math: the picture is primarily a mathematical equation or
    formula. Prefer encoding as digital math appropriate to THIS
    publication format (see Publication format rules). Prefer
    needs_attention; say so in teaching_note.
  - For image_of_math coaching: do NOT recommend MathJax (that is a
    rendering library, not an encoding). Do NOT recommend MathML alttext /
    alt-text attributes as the accessibility solution — that is not best
    practice; the math itself should be machine-readable.
  - Do not use image_of_table / image_of_math for ordinary photos, logos,
    charts that are genuinely graphical (bar/pie/line art), or decorative
    ornaments — only when the image is essentially a table or equation.
  - likely_wrong_orientation: text or scene content looks rotated, sideways,
    or upside-down relative to normal reading. Prefer needs_attention (or
    uncertain if unsure) so a human can re-check; do not invent a corrected
    alt that assumes a fixed rotation.
  - low_resolution: the raster looks too small / soft / pixelated for people
    who magnify the page (use Dimensions when provided; also visual softness).
    Prefer needs_attention for content images. Do not flag intentional tiny
    decorative icons, spacers, or vector art.
  - joined_images: the picture is visually several distinct images, panels,
    or photos joined into one raster (grid, side-by-side, before/after,
    labelled A/B/C scientific figure, slide with multiple pictures). Prefer
    needs_attention so a human can consider splitting the figure so each
    part can have its own alt text. Do not flag panoramas, double exposures,
    a single diagram with internal labels, a photo with one magnified inset,
    or comics/graphic-novel sequential art that is meant as one narrative
    figure.
- Document fit (flag even when the alt is otherwise accurate):
  - repeats_surrounding_text: the alt restates facts, names, captions, or
    explanations already given in the surrounding text, so a screen-reader
    user hears the same information twice. Flag when the overlap is
    substantial. Do not flag a shared proper name, a short figure number, or
    a brief restatement needed to identify the image. Prefer needs_attention.
    Do not flag when Surrounding text is "(none provided)", or when the
    image is decorative with empty alt.
  - wrong_language: the alt is written in a different natural language from
    the surrounding publication text (for example English alt next to French
    body copy). Infer the publication language from Surrounding text, not
    from the CheckMate UI language (UI language is only for reason and
    teaching_note). Prefer needs_attention. Do not flag when Surrounding
    text is "(none provided)", when the alt is empty or decorative, when the
    alt is only numbers, symbols, or proper names, or when both sides are
    too short to judge.
- Alt wording:
  - spelling_or_grammar: the alt has a clear spelling or grammar error.
    Prefer needs_attention. Do not flag:
    - empty alt, decorative images, or alt that is only numbers/symbols;
    - misspellings or non-standard grammar that match text visible in the
      image when the alt is quoting that text verbatim (signs, packaging,
      screenshots, handwritten notes);
    - noun-phrase alts, missing terminal punctuation, or telegram-style
      fragments — those are normal for alt text;
    - regional spelling that matches the publication (colour/color), or
      proper names, brands, and technical terms you are not sure about.
- Keep reason and teaching_note concise.
- Pass A heuristic flags in the user message are local checks (including
  pixel dimensions). Include each of those codes in "issues" unless you can
  clearly see they are wrong. Address every confirmed Pass A flag in
  teaching_note — do not drop low_resolution or joined_images just because
  another issue (for example image_of_math) is more interesting.
- The attached picture may be resized for the API. For low_resolution, trust
  Pass A and the Dimensions field more than how sharp the attachment looks.

Publication format rules:
{format_rules}
"""


def build_vision_user_text(
    image: AltExportImage,
    *,
    heuristic_flags: list[str],
    publication_format: str = "",
) -> str:
    alt = image.alt_stripped or "(none)"
    flags = ", ".join(heuristic_flags) if heuristic_flags else "(none)"
    ctx = image.context_stripped
    ctx_block = ctx if ctx else "(none provided)"
    fmt = publication_format_label(publication_format)
    key = infer_publication_format(explicit=publication_format)
    host_line = (
        "- Host format: HTML web page\n"
        if key == "html"
        else f"- Publication format: {fmt}\n"
    )
    return (
        "Assess this image's alt text / decorative status for document fit.\n"
        f"{host_line}"
        f"- Index: {image.index}\n"
        f"- Filename: {image.filename}\n"
        f"- Status: {image.status or '(unknown)'}\n"
        f"- Classification: {image.classification or '(none)'}\n"
        f"- Dimensions: {image.dimensions or '(unknown)'}\n"
        f"- Pass A heuristic flags (confirm or reject each; mention confirmed "
        f"ones in teaching_note): {flags}\n"
        f"- Alt text: {alt}\n"
        f"- Surrounding text:\n{ctx_block}\n"
    )


# Pass A codes that map onto the vision issue vocabulary.
_HEURISTIC_TO_ISSUE = {
    FLAG_MISSING_ALT: "missing_alt",
    FLAG_PLACEHOLDER_ALT: "placeholder_alt",
    FLAG_FILENAME_AS_ALT: "filename_as_alt",
    FLAG_EMPTY_HAS_ALT: "empty_has_alt",
    FLAG_DECORATIVE_WITH_ALT: "likely_decorative_with_alt",
    FLAG_DUPLICATE_ALT: "duplicate_alt",
    FLAG_CLASS_DECORATIVE_MISMATCH: "likely_content_marked_decorative",
    FLAG_LOW_RESOLUTION: "low_resolution",
    FLAG_JOINED_IMAGES: "joined_images",
}


def _teaching_for_pass_a_issue(code: str) -> str:
    return {
        "low_resolution": _(
            "The image is also low resolution and may fail under magnification."
        ),
        "joined_images": _(
            "This looks like several images joined into one; consider splitting "
            "so each part can have its own alt text."
        ),
        "likely_content_marked_decorative": _(
            "Classification suggests this is content, not decorative."
        ),
        "likely_decorative_with_alt": _(
            "Decorative images should not carry alt text."
        ),
        "missing_alt": _("This content image still needs alt text."),
        "empty_has_alt": _("The has-alt status does not match the empty alt."),
        "placeholder_alt": _("Replace the placeholder with a real description."),
        "filename_as_alt": _("Do not use the filename as alt text."),
        "duplicate_alt": _("This alt text is duplicated on another image."),
    }.get(code, "")


def apply_pass_a_flags_to_assessment(
    assessment: AltImageAssessment,
    heuristic_flags: list[str],
) -> AltImageAssessment:
    """Merge Pass A flags into vision issues and teaching_note when omitted."""
    extra: list[str] = []
    existing = list(assessment.issues)
    for flag in heuristic_flags:
        issue = _HEURISTIC_TO_ISSUE.get(flag)
        if issue and issue not in existing and issue not in extra:
            extra.append(issue)
    if not extra:
        return assessment
    issues = [i for i in existing if i != "ok"] + extra
    note = (assessment.teaching_note or "").strip()
    note_l = note.lower()
    snippets: list[str] = []
    for code in extra:
        snippet = _teaching_for_pass_a_issue(code)
        if not snippet:
            continue
        key = snippet.split(".")[0].lower()
        if key and key in note_l:
            continue
        if code.replace("_", " ") in note_l:
            continue
        snippets.append(snippet)
    if snippets:
        note = f"{note} {' '.join(snippets)}".strip() if note else " ".join(snippets)
    verdict = assessment.verdict
    if verdict == "ok":
        verdict = "needs_attention"
    return replace(assessment, issues=issues, teaching_note=note, verdict=verdict)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip optional fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _norm_enum(value: Any, allowed: frozenset[str], default: str) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
    return default


def _norm_issues(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if key in _ISSUE_VOCAB and key not in out:
            out.append(key)
    return out


def parse_vision_assessment(
    text: str,
    *,
    image: AltExportImage,
) -> AltImageAssessment | None:
    data = _extract_json_object(text)
    if data is None:
        return None
    status_ok = data.get("status_ok")
    if not isinstance(status_ok, bool):
        status_ok = True
    return AltImageAssessment(
        index=image.index,
        filename=image.filename,
        verdict=_norm_enum(data.get("verdict"), _VERDICTS, "uncertain"),
        confidence=_norm_enum(data.get("confidence"), _CONFIDENCE, "low"),
        status_ok=status_ok,
        recommended_status=_norm_enum(
            data.get("recommended_status"), _STATUSES, "has_alt"
        ),
        descriptiveness=_norm_enum(
            data.get("descriptiveness"), _QUALITY, "uncertain"
        ),
        accuracy=_norm_enum(data.get("accuracy"), _QUALITY, "uncertain"),
        usefulness=_norm_enum(data.get("usefulness"), _QUALITY, "uncertain"),
        issues=_norm_issues(data.get("issues")),
        reason=str(data.get("reason") or "").strip(),
        teaching_note=str(data.get("teaching_note") or "").strip(),
        suggested_alt=None,  # v1: never accept model rewrites
        pass_name="vision",
    )


def _repair_json_prompt() -> str:
    lang = _language_name()
    return (
        "Your previous reply was not valid JSON matching the required schema. "
        "Reply again with ONLY the JSON object, no markdown fences, no extra text. "
        f"Keep reason and teaching_note in {lang}."
    )


def build_synthesis_system_prompt(publication_format: str = "") -> str:
    lang = _language_name()
    lang_code = get_language()
    h1, h2, h3, h4, h5 = _section_headings()
    format_rules = _publication_format_rules(publication_format)
    key = infer_publication_format(explicit=publication_format)
    host = "web page" if key == "html" else "publication"
    return f"""You are an accessibility publishing assistant inside CheckMate.
You summarize an alt-text quality assessment for publishers and remediators who
may not know what good alt text looks like.

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write the entire reply in {lang}, including all headings.
- Do not use English unless the UI language is English.
- Filenames and issue codes may stay in their original form.

Structure your reply with these exact markdown headings (and no others as
top-level headings):

## {h1}
## {h2}
## {h3}
## {h4}
## {h5}

Rules:
- Use only the provided assessment data; do not invent images or findings.
- Lead with themes a non-expert can act on (especially decorative vs content,
  images of tables, images of math that need digital math, low-resolution
  images that fail under magnification, wrong orientation, joined /
  multi-panel figures that should be split, alt that repeats nearby prose,
  alt written in the wrong language, and spelling or grammar errors in the
  alt).
- If any assessed image has issues image_of_table, image_of_math,
  likely_wrong_orientation, low_resolution, joined_images,
  repeats_surrounding_text, wrong_language, or spelling_or_grammar, call those
  out explicitly in "{h2}" and "{h3}".
- In "{h3}", list the worst items first and cite Index and Filename.
- In "{h4}", give short coaching tied to what was found in this document
  and to the publication format rules below (including remediating table
  images, encoding math as digital math for THIS format, replacing
  low-resolution rasters with sharper assets, fixing rotation when relevant,
  splitting joined/multi-panel rasters so each part can have its own alt text,
  not repeating surrounding prose, writing alt in the same language as the {host},
  and fixing spelling or grammar unless the alt quotes text from the image).
  Do not recommend MathJax or MathML alttext
  as the fix for math images. Do not recommend EPUB extended-description
  techniques for PDF, or PDF associated-file MathML for EPUB/eBraille.
- In "{h5}", note sample vs full review, that AI can be wrong, and that this is
  assisted review — not a conformance certificate.
- Keep each section concise (short paragraphs or a few bullets).
- Use markdown so the reply can be shown as HTML.

Publication format rules:
{format_rules}
"""


def build_synthesis_user_prompt(
    *,
    export: AltExport,
    heuristics: HeuristicReport,
    sample: SamplePlan,
    assessments: list[AltImageAssessment],
) -> str:
    lang = _language_name()
    counts = export.counts()
    needs = sum(1 for a in assessments if a.verdict == "needs_attention")
    ok_n = sum(1 for a in assessments if a.verdict == "ok")
    caveat = sum(1 for a in assessments if a.verdict == "likely_ok_with_caveat")
    uncertain = sum(1 for a in assessments if a.verdict == "uncertain")
    lines = [
        f"Summarize this alt-text assessment. Reply entirely in {lang}.",
        f"- Document: {export.document_name}",
        f"- Publication format: {publication_format_label(export.publication_format)}",
        f"- Total images: {counts['total']}",
        f"- With alt text (export status): {counts['with_alt']}",
        f"- Decorative (export status): {counts['decorative']}",
        f"- Missing alt (derived): {counts['missing']}",
        f"- Assessment mode: {sample.mode}",
        f"- AI-reviewed images: {len(assessments)} of {sample.total_images}",
        f"- Verdicts among AI-reviewed: needs_attention={needs}, ok={ok_n}, "
        f"likely_ok_with_caveat={caveat}, uncertain={uncertain}",
        "",
        summarize_heuristics(heuristics),
        "",
        "Per-image AI findings (JSON lines):",
    ]
    for a in assessments:
        lines.append(json.dumps(a.to_json_dict(), ensure_ascii=False))
    return "\n".join(lines)


def _looks_truncated(text: str, finish_reason: str | None) -> bool:
    if (finish_reason or "").lower() in {"length", "max_tokens"}:
        return True
    t = text or ""
    if not t.strip():
        return False
    return t.count("```") % 2 == 1


def _merge_continuation(first: str, second: str) -> str:
    a = (first or "").rstrip()
    b = (second or "").lstrip()
    if not b:
        return a
    if not a:
        return b
    return f"{a}\n\n{b}"


def _continue_prompt() -> str:
    lang = _language_name()
    return (
        f"Your previous reply was cut off before it finished.\n"
        f"Continue from exactly where you stopped. "
        f"Do not repeat completed sections. "
        f"Close any open code fences. "
        f"Reply entirely in {lang}."
    )


def assessment_sidecar_dict(
    *,
    export: AltExport,
    heuristics: HeuristicReport,
    sample: SamplePlan,
    assessments: list[AltImageAssessment],
    synthesis_markdown: str,
) -> dict[str, Any]:
    return {
        "document": export.document_name,
        "folder": str(export.folder),
        "counts": export.counts(),
        "sample": {
            "mode": sample.mode,
            "percent": sample.percent,
            "size": sample.size,
            "total_images": sample.total_images,
            "indices": sample.indices,
            "reasons": sample.reasons,
        },
        "heuristics": {
            "flag_counts": heuristics.flag_counts,
            "findings": [
                {
                    "index": f.index,
                    "filename": f.filename,
                    "flags": f.flags,
                }
                for f in heuristics.findings
            ],
        },
        "publication_format": infer_publication_format(
            explicit=export.publication_format,
            document_name=export.document_name,
            folder=export.folder,
        ),
        "assessments": [a.to_json_dict() for a in assessments],
        "synthesis_markdown": synthesis_markdown,
        "suggested_alt_phase": "v2_reserved",
    }


def write_assessment_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _merge_assessments(
    prior: list[AltImageAssessment],
    new: list[AltImageAssessment],
) -> list[AltImageAssessment]:
    by_index = {a.index: a for a in prior}
    for a in new:
        by_index[a.index] = a
    return [by_index[i] for i in sorted(by_index)]


def _is_fatal_vision_error(error: ProviderError) -> bool:
    return error.error_key in _FATAL_VISION_KEYS or _fatal_vision_provider_error(
        error.error_key, error.detail
    )


class _VisionProgress:
    """Thread-safe finished / in-flight / ETA status for a vision batch."""

    def __init__(
        self,
        *,
        total: int,
        workers: int,
        status_callback: StatusCallback | None,
    ) -> None:
        self.total = total
        self.workers = workers
        self._status_callback = status_callback
        self._lock = threading.Lock()
        self._done = 0
        self._verdicts = {
            "ok": 0,
            "needs_attention": 0,
            "likely_ok_with_caveat": 0,
            "uncertain": 0,
        }
        self._note = ""
        self._started = time.perf_counter()

    def set_workers(self, workers: int) -> None:
        with self._lock:
            self.workers = max(1, int(workers))

    def set_note(self, note: str) -> None:
        with self._lock:
            self._note = (note or "").strip()
            msg = self._message_locked()
        _status(self._status_callback, msg)

    def mark_start(self, image: AltExportImage) -> None:
        with self._lock:
            msg = self._message_locked()
        _status(self._status_callback, msg)

    def mark_done(
        self,
        image: AltExportImage,
        *,
        counted: bool = True,
        verdict: str = "",
    ) -> None:
        with self._lock:
            if counted:
                self._done += 1
                key = (verdict or "uncertain").strip().lower()
                if key not in self._verdicts:
                    key = "uncertain"
                self._verdicts[key] += 1
            msg = self._message_locked()
        _status(self._status_callback, msg)

    def _message_locked(self) -> str:
        return vision_progress_message(
            done=self._done,
            total=self.total,
            elapsed_s=time.perf_counter() - self._started,
            verdicts=dict(self._verdicts),
            note=self._note,
        )


def _error_assessment(
    image: AltExportImage,
    *,
    reason: str,
    error: str,
    teaching_note: str = "",
    issues: list[str] | None = None,
    status_ok: bool = True,
    recommended_status: str = "has_alt",
) -> AltImageAssessment:
    return AltImageAssessment(
        index=image.index,
        filename=image.filename,
        verdict="uncertain",
        confidence="low",
        status_ok=status_ok,
        recommended_status=recommended_status,
        issues=list(issues or []),
        reason=reason,
        teaching_note=teaching_note,
        pass_name="error",
        error=error,
    )


def _assess_one_image(
    image: AltExportImage,
    *,
    flags: list[str],
    pub_fmt: str,
    system: str,
    session: ExplainSession,
    cost_lock: threading.Lock,
    cancel_event: threading.Event | None,
) -> tuple[AltImageAssessment | None, ProviderError | None, bool]:
    """Vision-assess one export image.

    Returns ``(assessment, fatal_error, rate_limited)``.
    """
    if _cancelled(cancel_event):
        return None, None, False
    if image.image_path is None or not image.image_path.is_file():
        return (
            _error_assessment(
                image,
                reason=_("Image file was missing from the export folder."),
                teaching_note=_(
                    "Re-export the publication so every CSV row has an image file."
                ),
                issues=["missing_alt"],
                status_ok=False,
                recommended_status="missing",
                error="missing_file",
            ),
            None,
            False,
        )

    try:
        data_uri, _mime = encode_image_for_vision(image.image_path)
    except Exception as e:
        logger.exception("Failed to encode image %s", image.filename)
        return (
            _error_assessment(image, reason=str(e), error="encode_failed"),
            None,
            False,
        )

    if _cancelled(cancel_event):
        return None, None, False

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": build_vision_user_text(
                image, heuristic_flags=flags, publication_format=pub_fmt
            ),
        },
        _vision_image_url_part(data_uri, session.model),
    ]
    try:
        vision_session = ExplainSession(
            model=session.model,
            api_key=session.api_key,
            api_base=session.api_base,
        )
        text = vision_session.ask_multimodal(
            system=system,
            user_content=user_content,
            max_tokens=_VISION_MAX_TOKENS,
            operation="alt_vision",
        )
        parsed = parse_vision_assessment(text, image=image)
        if parsed is None:
            try:
                repair = vision_session.followup(
                    _repair_json_prompt(), max_tokens=_VISION_MAX_TOKENS
                )
                parsed = parse_vision_assessment(repair, image=image)
            except Exception:
                logger.exception(
                    "Vision JSON repair failed for %s", image.filename
                )
        with cost_lock:
            session.session_cost_usd += vision_session.session_cost_usd
            session.session_prompt_tokens += vision_session.session_prompt_tokens
            session.session_completion_tokens += (
                vision_session.session_completion_tokens
            )
        if parsed is None:
            return (
                _error_assessment(
                    image,
                    reason=_("The AI returned an unreadable assessment."),
                    error="bad_json",
                ),
                None,
                False,
            )
        return apply_pass_a_flags_to_assessment(parsed, flags), None, False
    except ProviderError as e:
        assessment = _error_assessment(
            image,
            reason=e.detail or e.error_key,
            error=e.error_key,
        )
        if _is_rate_limit_error(e.error_key, e.detail):
            return assessment, None, True
        if _is_fatal_vision_error(e):
            return assessment, e, False
        return assessment, None, False
    except Exception as e:
        logger.exception("Vision assess failed for %s", image.filename)
        return (
            _error_assessment(
                image, reason=str(e), error="provider_error"
            ),
            None,
            False,
        )


def _backoff_wait(
    seconds: float,
    cancel_event: threading.Event | None,
    stop: threading.Event,
) -> None:
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        if _cancelled(cancel_event) or stop.is_set():
            return
        step = min(0.25, remaining)
        time.sleep(step)
        remaining -= step


def _run_vision_batch(
    to_review: list[int],
    *,
    by_index: dict[int, AltExportImage],
    findings_by_index: dict[int, Any],
    pub_fmt: str,
    system: str,
    session: ExplainSession,
    cancel_event: threading.Event | None,
    status_callback: StatusCallback | None,
    max_workers: int,
) -> tuple[list[AltImageAssessment], ProviderError | None]:
    """Assess *to_review* in waves. Rate limits halve the next wave's workers."""
    total = len(to_review)
    if total == 0:
        return [], None
    workers = max(1, min(int(max_workers), total, _MAX_VISION_WORKERS))
    logger.info(
        "Alt vision batch starting images=%s workers=%s model=%s",
        total,
        workers,
        session.model,
    )
    cost_lock = threading.Lock()
    stop = threading.Event()
    progress = _VisionProgress(
        total=total, workers=workers, status_callback=status_callback
    )
    assessments: list[AltImageAssessment] = []
    fatal_error: ProviderError | None = None
    retries: dict[int, int] = {}
    current_workers = workers
    backoff_s = 0.0
    remaining = list(to_review)

    def work(
        index: int,
    ) -> tuple[AltImageAssessment | None, ProviderError | None, bool]:
        if _cancelled(cancel_event) or stop.is_set():
            return None, None, False
        image = by_index.get(index)
        if image is None:
            return None, None, False
        flags = (
            list(findings_by_index[index].flags)
            if index in findings_by_index
            else []
        )
        counted = False
        verdict = ""
        progress.mark_start(image)
        try:
            if _cancelled(cancel_event) or stop.is_set():
                return None, None, False
            assessment, fatal, rate_limited = _assess_one_image(
                image,
                flags=flags,
                pub_fmt=pub_fmt,
                system=system,
                session=session,
                cost_lock=cost_lock,
                cancel_event=cancel_event,
            )
            counted = assessment is not None and not rate_limited
            if counted and assessment is not None:
                verdict = assessment.verdict
            return assessment, fatal, rate_limited
        finally:
            progress.mark_done(image, counted=counted, verdict=verdict)

    def _unpack(
        fut: Any, index: int
    ) -> tuple[AltImageAssessment | None, ProviderError | None, bool]:
        nonlocal fatal_error
        image = by_index.get(index)
        if fut.cancelled():
            return None, None, False
        try:
            result = fut.result()
            if len(result) == 2:
                assessment, fatal = result
                return assessment, fatal, False
            return result
        except CancelledError:
            return None, None, False
        except Exception as e:
            logger.exception(
                "Vision worker crashed for %s",
                image.filename if image is not None else index,
            )
            if image is None:
                return None, None, False
            return (
                _error_assessment(image, reason=str(e), error="provider_error"),
                None,
                False,
            )

    pool = ThreadPoolExecutor(max_workers=workers)
    in_flight: dict[Any, int] = {}
    wave_had_rate_limit = False
    try:
        while remaining or in_flight:
            if _cancelled(cancel_event) or stop.is_set():
                break
            if not in_flight:
                if wave_had_rate_limit:
                    new_workers = max(1, current_workers // 2)
                    if new_workers < current_workers:
                        logger.warning(
                            "Rate limit detected; reducing parallel workers "
                            "from %s to %s",
                            current_workers,
                            new_workers,
                        )
                        current_workers = new_workers
                        progress.set_workers(current_workers)
                    backoff_s = min(
                        _RATE_LIMIT_BACKOFF_MAX_S,
                        (
                            _RATE_LIMIT_BACKOFF_INITIAL_S
                            if backoff_s <= 0
                            else backoff_s * 2
                        ),
                    )
                    progress.set_note(_("Rate limited; slowing down…"))
                    _backoff_wait(backoff_s, cancel_event, stop)
                    progress.set_note("")
                    wave_had_rate_limit = False
                    if _cancelled(cancel_event) or stop.is_set():
                        break
                wave: list[int] = []
                while remaining and len(wave) < current_workers:
                    wave.append(remaining.pop(0))
                if not wave:
                    break
                in_flight = {pool.submit(work, index): index for index in wave}
            done_set, _still = wait(
                set(in_flight), timeout=0.25, return_when=FIRST_COMPLETED
            )
            if not done_set:
                continue
            for fut in done_set:
                index = in_flight.pop(fut)
                assessment, fatal, rate_limited = _unpack(fut, index)
                if rate_limited:
                    n = retries.get(index, 0) + 1
                    retries[index] = n
                    if n <= _RATE_LIMIT_MAX_RETRIES:
                        wave_had_rate_limit = True
                        remaining.insert(0, index)
                    elif assessment is not None:
                        assessments.append(assessment)
                    continue
                if assessment is not None:
                    assessments.append(assessment)
                if fatal is not None and fatal_error is None:
                    fatal_error = fatal
                    stop.set()
            if stop.is_set():
                break
    finally:
        aborting = _cancelled(cancel_event) or stop.is_set()
        pool.shutdown(wait=True, cancel_futures=aborting)
        for fut, index in list(in_flight.items()):
            if not fut.done():
                continue
            assessment, fatal, rate_limited = _unpack(fut, index)
            if assessment is not None and not rate_limited:
                assessments.append(assessment)
            if fatal is not None and fatal_error is None:
                fatal_error = fatal
    return assessments, fatal_error


def assess_alt_export(
    folder: Path | str,
    *,
    mode: str = "percent",
    percent: int = DEFAULT_SAMPLE_PERCENT,
    sample_size: int | None = None,
    prior: AltAssessResult | None = None,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
    write_json: bool = True,
    export: AltExport | None = None,
    skip_credentials_check: bool = False,
    max_workers: int | None = None,
) -> AltAssessResult:
    """Run Pass A + vision sample/all + document synthesis on an export folder.

    When *prior* is provided, already-assessed indices are skipped and new
    findings are merged before re-synthesis (assess more).

    *export* may be a pre-loaded ``AltExport`` (avoids reading the folder twice).
    *skip_credentials_check* is for hosts that already resolved a working model
    (e.g. Fido's bridged session).
    *max_workers* overrides the default parallel vision worker count.
    """
    if _cancelled(cancel_event):
        return AltAssessResult(ok=False, error_key="cancelled")

    if not litellm_available():
        return AltAssessResult(ok=False, error_key="no_litellm")

    # Credentials first so the UI can update before export I/O / heuristics.
    if not skip_credentials_check:
        _status(status_callback, _("Checking AI credentials…"))
        ok, err = ensure_credentials_ready()
        if not ok:
            return AltAssessResult(ok=False, error_key=err or "no_key")
    if _cancelled(cancel_event):
        return AltAssessResult(ok=False, error_key="cancelled")

    prior_assessments = list(prior.assessments) if prior is not None else []
    prior_by_index = {a.index: a for a in prior_assessments}
    exclude = set(prior_by_index)

    _status(status_callback, _("Loading export…"))
    try:
        if export is None:
            export = load_alt_export(folder)
    except FileNotFoundError as e:
        return AltAssessResult(ok=False, error_key="bad_export", detail=str(e))
    except ValueError as e:
        return AltAssessResult(ok=False, error_key="bad_export", detail=str(e))

    if _cancelled(cancel_event):
        return AltAssessResult(
            ok=False,
            error_key="cancelled",
            export=export,
        )

    _status(status_callback, _("Analyzing alt text…"))
    heuristics = run_heuristics(export)
    batch = build_sample_plan(
        export,
        heuristics,
        mode=mode,
        percent=percent,
        sample_size=sample_size,
        exclude_indices=exclude,
    )
    sample = merge_sample_plans(prior.sample if prior else None, batch)

    if _cancelled(cancel_event):
        return AltAssessResult(
            ok=False,
            error_key="cancelled",
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=prior_assessments,
        )

    session: ExplainSession | None = prior.session if prior is not None else None
    if session is None:
        try:
            session = ExplainSession.create()
        except RuntimeError as e:
            return AltAssessResult(
                ok=False,
                error_key=str(e) or "no_key",
                export=export,
                heuristics=heuristics,
                sample=sample,
                assessments=prior_assessments,
            )
    assert session is not None

    if not model_likely_supports_vision(session.model):
        return AltAssessResult(
            ok=False,
            error_key="no_vision",
            detail=session.model,
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=prior_assessments,
        )

    if prior is None or not session.messages:
        _status(status_callback, _("Checking AI connection…"))
        conn_ok, conn_err, conn_detail = session.check_connection(
            cancel_event=cancel_event
        )
        if not conn_ok:
            return AltAssessResult(
                ok=False,
                error_key=conn_err or "network",
                detail=conn_detail,
                session=session,
                export=export,
                heuristics=heuristics,
                sample=sample,
                assessments=prior_assessments,
            )
    if _cancelled(cancel_event):
        return AltAssessResult(
            ok=False,
            error_key="cancelled",
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=prior_assessments,
        )

    pub_fmt = infer_publication_format(
        explicit=export.publication_format,
        document_name=export.document_name,
        folder=export.folder,
    )
    export.publication_format = pub_fmt

    by_index = {im.index: im for im in export.images}
    findings_by_index = heuristics.by_index()
    system = build_vision_system_prompt(publication_format=pub_fmt)
    to_review = [i for i in batch.indices if i not in prior_by_index]
    workers = vision_parallel_workers(session.model, requested=max_workers)
    new_assessments, fatal = _run_vision_batch(
        to_review,
        by_index=by_index,
        findings_by_index=findings_by_index,
        pub_fmt=pub_fmt,
        system=system,
        session=session,
        cancel_event=cancel_event,
        status_callback=status_callback,
        max_workers=workers,
    )
    assessments = _merge_assessments(prior_assessments, new_assessments)

    if fatal is not None:
        return AltAssessResult(
            ok=False,
            error_key=fatal.error_key,
            detail=fatal.detail,
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=assessments,
        )

    if _cancelled(cancel_event):
        return AltAssessResult(
            ok=False,
            error_key="cancelled",
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=assessments,
        )

    _status(status_callback, _("Writing assessment summary…"))
    try:
        synth = session.ask(
            system=build_synthesis_system_prompt(publication_format=pub_fmt),
            user=build_synthesis_user_prompt(
                export=export,
                heuristics=heuristics,
                sample=sample,
                assessments=assessments,
            ),
            max_tokens=_SYNTH_MAX_TOKENS,
        )
    except ProviderError as e:
        return AltAssessResult(
            ok=False,
            error_key=e.error_key,
            detail=e.detail,
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=assessments,
        )
    except Exception as e:
        logger.exception("Alt assessment synthesis failed")
        return AltAssessResult(
            ok=False,
            error_key="provider_error",
            detail=str(e),
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=assessments,
        )

    if _looks_truncated(synth, session.last_finish_reason):
        try:
            cont = session.followup(_continue_prompt(), max_tokens=_SYNTH_MAX_TOKENS)
            synth = _merge_continuation(synth, cont)
        except Exception:
            logger.exception("Synthesis continuation failed")

    if not (synth or "").strip():
        return AltAssessResult(
            ok=False,
            error_key="empty_response",
            session=session,
            export=export,
            heuristics=heuristics,
            sample=sample,
            assessments=assessments,
        )

    json_path = None
    if write_json:
        try:
            payload = assessment_sidecar_dict(
                export=export,
                heuristics=heuristics,
                sample=sample,
                assessments=assessments,
                synthesis_markdown=synth,
            )
            json_path = write_assessment_json(
                export.folder / "alt_text_assessment.json", payload
            )
        except OSError:
            logger.exception("Could not write alt_text_assessment.json")

    logger.info(
        "Alt assessment done model=%s reviewed=%s mode=%s cost_usd=%s",
        session.model,
        len(assessments),
        sample.mode,
        session.session_cost_usd or "n/a",
    )
    return AltAssessResult(
        ok=True,
        text=synth,
        session=session,
        export=export,
        heuristics=heuristics,
        sample=sample,
        assessments=assessments,
        json_path=json_path,
        model=session.model or "",
    )


def ask_alt_assess_followup(
    session: ExplainSession,
    question: str,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> ExplainResult:
    """Answer a free-form follow-up about an existing alt assessment session."""
    q = (question or "").strip()
    if not q:
        return ExplainResult(ok=False, error_key="empty_question", session=session)
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    if not session.messages:
        return ExplainResult(ok=False, error_key="no_session", session=session)

    lang = _language_name()
    h1, h2, h3, h4, h5 = _section_headings()
    _status(status_callback, _("Thinking…"))
    try:
        text = session.followup(
            f"Follow-up question about the same alt-text assessment.\n"
            f"Reply entirely in {lang}.\n\n"
            f"Answer this question directly in a natural, conversational way. "
            f"Do NOT reuse the structured layout with headings such as "
            f"## {h1}, ## {h2}, ## {h3}, ## {h4}, or ## {h5}. "
            f"Prefer short paragraphs or a few bullets. "
            f"Stay focused on what was asked.\n\n"
            f"Question:\n{q}"
        )
    except ProviderError as e:
        return ExplainResult(
            ok=False, error_key=e.error_key, text=e.detail, session=session
        )
    except Exception as e:
        logger.exception("Alt assessment follow-up failed")
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)
    return ExplainResult(ok=True, text=text, session=session)


def preflight_message(
    folder: Path | str,
    *,
    mode: str = "percent",
    percent: int = DEFAULT_SAMPLE_PERCENT,
    model_name: str = "",
) -> tuple[AltExport, HeuristicReport, SamplePlan, str]:
    """Load export and build sample plan for the confirmation dialog."""
    export = load_alt_export(folder)
    heuristics = run_heuristics(export)
    sample = build_sample_plan(export, heuristics, mode=mode, percent=percent)
    return export, heuristics, sample, describe_sample_plan(sample, model_name)
