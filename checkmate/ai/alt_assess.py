"""AI vision assessment of alt text from a Fido export."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..i18n import _, get_language, language_display_name
from .alt_export import AltExport, AltExportImage, load_alt_export
from .alt_heuristics import HeuristicReport, run_heuristics, summarize_heuristics
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


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _status(cb: StatusCallback | None, message: str) -> None:
    if cb is not None:
        try:
            cb(message)
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
    try:
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


def build_vision_system_prompt() -> str:
    lang = _language_name()
    lang_code = get_language()
    return f"""You are an accessibility publishing assistant inside CheckMate.
You review one image from an EPUB/PDF/eBraille publication together with its
declared alt text status, any existing alt text, and optional surrounding page
text from the publication.

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
  image_of_table, image_of_math, likely_wrong_orientation, low_resolution, ok
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
    formula. Prefer encoding as digital math appropriate to the format
    (LaTeX, MathML, or OMML as fits the publication type). For PDF, the
    equation image may remain if it is tagged/associated with MathML.
    Prefer needs_attention; say so in teaching_note.
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
- Keep reason and teaching_note concise.
"""


def build_vision_user_text(image: AltExportImage, *, heuristic_flags: list[str]) -> str:
    alt = image.alt_stripped or "(none)"
    flags = ", ".join(heuristic_flags) if heuristic_flags else "(none)"
    ctx = image.context_stripped
    ctx_block = ctx if ctx else "(none provided)"
    return (
        "Assess this image's alt text / decorative status for document fit.\n"
        f"- Index: {image.index}\n"
        f"- Filename: {image.filename}\n"
        f"- Status: {image.status or '(unknown)'}\n"
        f"- Classification: {image.classification or '(none)'}\n"
        f"- Dimensions: {image.dimensions or '(unknown)'}\n"
        f"- Pass A heuristic flags: {flags}\n"
        f"- Alt text: {alt}\n"
        f"- Surrounding text:\n{ctx_block}\n"
    )


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


def build_synthesis_system_prompt() -> str:
    lang = _language_name()
    lang_code = get_language()
    h1, h2, h3, h4, h5 = _section_headings()
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
  images that fail under magnification, and wrong orientation).
- If any assessed image has issues image_of_table, image_of_math,
  likely_wrong_orientation, or low_resolution, call those out explicitly in
  "{h2}" and "{h3}" — they matter even when alt text itself looks fine.
- In "{h3}", list the worst items first and cite Index and Filename.
- In "{h4}", give short coaching tied to what was found in this document
  (including remediating table images, encoding math as digital math —
  LaTeX/MathML/OMML as appropriate, or PDF equation images tagged with
  MathML — replacing low-resolution rasters with sharper assets, and fixing
  rotation when relevant). Do not recommend MathJax or MathML alttext as the
  fix for math images.
- In "{h5}", note sample vs full review, that AI can be wrong, and that this is
  assisted review — not a conformance certificate.
- Keep each section concise (short paragraphs or a few bullets).
- Use markdown so the reply can be shown as HTML.
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
) -> AltAssessResult:
    """Run Pass A + vision sample/all + document synthesis on an export folder.

    When *prior* is provided, already-assessed indices are skipped and new
    findings are merged before re-synthesis (assess more).

    *export* may be a pre-loaded ``AltExport`` (avoids reading the folder twice).
    *skip_credentials_check* is for hosts that already resolved a working model
    (e.g. Fido's bridged session).
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

    by_index = {im.index: im for im in export.images}
    findings_by_index = heuristics.by_index()
    system = build_vision_system_prompt()
    new_assessments: list[AltImageAssessment] = []
    to_review = [i for i in batch.indices if i not in prior_by_index]
    total = len(to_review)

    for i, index in enumerate(to_review, start=1):
        if _cancelled(cancel_event):
            merged = _merge_assessments(prior_assessments, new_assessments)
            return AltAssessResult(
                ok=False,
                error_key="cancelled",
                session=session,
                export=export,
                heuristics=heuristics,
                sample=sample,
                assessments=merged,
            )
        image = by_index.get(index)
        if image is None:
            continue
        _status(
            status_callback,
            _("Assessing image {current} of {total}…").format(
                current=i, total=total
            ),
        )
        if image.image_path is None or not image.image_path.is_file():
            new_assessments.append(
                AltImageAssessment(
                    index=image.index,
                    filename=image.filename,
                    verdict="uncertain",
                    confidence="low",
                    status_ok=False,
                    recommended_status="missing",
                    issues=["missing_alt"],
                    reason=_("Image file was missing from the export folder."),
                    teaching_note=_(
                        "Re-export the publication so every CSV row has an image file."
                    ),
                    pass_name="error",
                    error="missing_file",
                )
            )
            continue

        flags = (
            list(findings_by_index[index].flags) if index in findings_by_index else []
        )
        try:
            data_uri, _mime = encode_image_for_vision(image.image_path)
        except Exception as e:
            logger.exception("Failed to encode image %s", image.filename)
            new_assessments.append(
                AltImageAssessment(
                    index=image.index,
                    filename=image.filename,
                    verdict="uncertain",
                    confidence="low",
                    reason=str(e),
                    pass_name="error",
                    error="encode_failed",
                )
            )
            continue

        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": build_vision_user_text(image, heuristic_flags=flags),
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
            session.session_cost_usd += vision_session.session_cost_usd
            session.session_prompt_tokens += vision_session.session_prompt_tokens
            session.session_completion_tokens += (
                vision_session.session_completion_tokens
            )
            if parsed is None:
                new_assessments.append(
                    AltImageAssessment(
                        index=image.index,
                        filename=image.filename,
                        verdict="uncertain",
                        confidence="low",
                        reason=_("The AI returned an unreadable assessment."),
                        pass_name="error",
                        error="bad_json",
                    )
                )
            else:
                new_assessments.append(parsed)
        except ProviderError as e:
            new_assessments.append(
                AltImageAssessment(
                    index=image.index,
                    filename=image.filename,
                    verdict="uncertain",
                    confidence="low",
                    reason=e.detail or e.error_key,
                    pass_name="error",
                    error=e.error_key,
                )
            )
            if e.error_key in {"no_key", "no_model", "network", "timeout"} or _fatal_vision_provider_error(
                e.error_key, e.detail
            ):
                merged = _merge_assessments(prior_assessments, new_assessments)
                return AltAssessResult(
                    ok=False,
                    error_key=e.error_key,
                    detail=e.detail,
                    session=session,
                    export=export,
                    heuristics=heuristics,
                    sample=sample,
                    assessments=merged,
                )
        except Exception as e:
            logger.exception("Vision assess failed for %s", image.filename)
            new_assessments.append(
                AltImageAssessment(
                    index=image.index,
                    filename=image.filename,
                    verdict="uncertain",
                    confidence="low",
                    reason=str(e),
                    pass_name="error",
                    error="provider_error",
                )
            )

    assessments = _merge_assessments(prior_assessments, new_assessments)

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
            system=build_synthesis_system_prompt(),
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
