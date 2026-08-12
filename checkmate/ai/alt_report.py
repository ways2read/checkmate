"""Render alt-text assessment HTML reports."""

from __future__ import annotations

import base64
import html
import logging
from pathlib import Path

from ..i18n import _, get_language, get_text_direction
from .alt_assess import AltAssessResult, AltImageAssessment
from .alt_export import AltExport, AltExportImage
from .alt_heuristics import HeuristicReport
from .markdown_html import (
    _WEBVIEW_TAB_EXIT_SCRIPT,
    _ai_browser_css,
    _structure_ai_browser_body,
    markdown_to_body_html,
    with_ai_disclaimer,
)

logger = logging.getLogger(__name__)

# WebView2 SetPage blocks file:// image loads; embed data URIs instead.
# Cache avoids re-encoding on follow-up repaints of the same report.
_THUMB_CACHE: dict[tuple[str, int, int, int], str] = {}
_THUMB_MAX_EDGE = 160
_THUMB_JPEG_QUALITY = 55
# Larger preview for click-to-enlarge (still embedded; file:// blocked in WebView).
_PREVIEW_MAX_EDGE = 800
_PREVIEW_JPEG_QUALITY = 72


def _thumb_data_uri(
    path: Path | None,
    *,
    max_edge: int = _THUMB_MAX_EDGE,
    jpg_quality: int = _THUMB_JPEG_QUALITY,
) -> str:
    """JPEG data-URI suitable for WebView ``SetPage`` (not file://)."""
    if path is None or not path.is_file():
        return ""
    try:
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        return ""
    cache_key = (str(resolved), max_edge, jpg_quality, mtime_ns)
    cached = _THUMB_CACHE.get(cache_key)
    if cached is not None:
        return cached

    uri = ""
    try:
        import fitz  # type: ignore

        pix = fitz.Pixmap(str(resolved))
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        longest = max(pix.width, pix.height) or 1
        if longest > max_edge:
            scale = max_edge / float(longest)
            pix = fitz.Pixmap(
                pix,
                max(1, int(round(pix.width * scale))),
                max(1, int(round(pix.height * scale))),
            )
        data = pix.tobytes("jpeg", jpg_quality=jpg_quality)
        b64 = base64.standard_b64encode(data).decode("ascii")
        uri = f"data:image/jpeg;base64,{b64}"
    except Exception:
        logger.debug("Fast thumb encode failed for %s", resolved, exc_info=True)
        try:
            raw = resolved.read_bytes()
            # Full-size embed only when small enough (fallback path).
            limit = 200_000 if max_edge <= _THUMB_MAX_EDGE else 1_500_000
            if len(raw) <= limit:
                suffix = resolved.suffix.lower()
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(suffix, "image/jpeg")
                b64 = base64.standard_b64encode(raw).decode("ascii")
                uri = f"data:{mime};base64,{b64}"
        except OSError:
            uri = ""

    if uri:
        # Bound memory if many reports are opened in one session.
        if len(_THUMB_CACHE) > 400:
            _THUMB_CACHE.clear()
        _THUMB_CACHE[cache_key] = uri
    return uri


def _thumb_src(path: Path | None, **_unused: object) -> str:
    """Always return an embedded data-URI (Edge WebView blocks file:// from SetPage)."""
    return _thumb_data_uri(path)


def _clickable_thumb_html(
    path: Path | None, *, full_preview: bool = False
) -> str:
    """Card thumbnail that opens a lightbox preview on click.

    In the in-app WebView (*full_preview* False), reuse the small thumb for the
    lightbox so follow-up ``SetPage`` / close teardown stay lightweight. Saved
    or browser HTML embeds a larger JPEG preview.
    """
    thumb = _thumb_data_uri(path)
    if not thumb:
        return '<div class="no-thumb"></div>'
    if full_preview:
        full = (
            _thumb_data_uri(
                path, max_edge=_PREVIEW_MAX_EDGE, jpg_quality=_PREVIEW_JPEG_QUALITY
            )
            or thumb
        )
    else:
        full = thumb
    return (
        f'<img src="{html.escape(thumb, quote=True)}" '
        f'data-full-src="{html.escape(full, quote=True)}" '
        f'alt="" width="120" height="90" loading="lazy" '
        f'title="{html.escape(_("Click to enlarge"))}" '
        f'onclick="openModal(this)">'
    )



def _verdict_label(verdict: str) -> str:
    return {
        "ok": _("Likely OK"),
        "needs_attention": _("Needs attention"),
        "likely_ok_with_caveat": _("OK with caveat"),
        "uncertain": _("Uncertain"),
    }.get(verdict, verdict)


def _verdict_class(verdict: str) -> str:
    return {
        "ok": "ok",
        "needs_attention": "needs",
        "likely_ok_with_caveat": "caveat",
        "uncertain": "uncertain",
    }.get(verdict, "uncertain")


def _filter_bucket(a: AltImageAssessment) -> str:
    if a.pass_name == "heuristic":
        return "heuristics"
    if a.verdict == "needs_attention":
        if "likely_content_marked_decorative" in a.issues:
            return "decorative"
        return "needs"
    if a.verdict in {"ok", "likely_ok_with_caveat"}:
        return "ok"
    return "uncertain"


def _stats_html(export: AltExport, result: AltAssessResult, *, title: str) -> str:
    counts = export.counts()
    reviewed = len(result.assessments)
    needs = sum(1 for a in result.assessments if a.verdict == "needs_attention")
    mode = result.sample.mode if result.sample else "sample"
    boxes = [
        (str(counts["total"]), _("Total images"), ""),
        (str(counts["with_alt"]), _("With alt text"), "ok"),
        (str(counts["decorative"]), _("Decorative"), ""),
        (str(counts["missing"]), _("Missing alt"), "needs" if counts["missing"] else ""),
        (str(reviewed), _("AI reviewed"), ""),
        (str(needs), _("Needs attention"), "needs" if needs else "ok"),
    ]
    parts = [
        '<header class="doc-header">',
        f'<p class="doc-eyebrow">{html.escape(_("Alt text"))}</p>',
        f"<h1>{html.escape(title)}</h1>",
        f'<p class="meta"><strong>{html.escape(_("Document:"))}</strong> '
        f"{html.escape(export.document_name)}</p>",
        f'<p class="meta"><strong>{html.escape(_("Mode:"))}</strong> '
        f"{html.escape(mode)} — {reviewed}/{counts['total']}</p>",
        "</header>",
        '<div class="stat-row" role="group" '
        f'aria-label="{html.escape(_("Image counts"))}">',
    ]
    for number, label, klass in boxes:
        cls = f"stat-box {klass}".strip() if klass else "stat-box"
        parts.append(
            f'<div class="{cls}"><div class="number">{html.escape(number)}</div>'
            f'<div class="label">{html.escape(label)}</div></div>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def _priority_cards_html(
    export: AltExport,
    assessments: list[AltImageAssessment],
    heuristics: HeuristicReport | None,
    *,
    full_preview: bool = False,
) -> str:
    by_index = {im.index: im for im in export.images}
    # Worst first
    order = {"needs_attention": 0, "uncertain": 1, "likely_ok_with_caveat": 2, "ok": 3}
    sorted_a = sorted(
        assessments,
        key=lambda a: (order.get(a.verdict, 9), a.index),
    )
    heur_by = heuristics.by_index() if heuristics else {}

    cards: list[str] = [
        f'<section class="priority" aria-label="{html.escape(_("Priority findings"))}">',
        f"<h2>{html.escape(_('Priority findings'))}</h2>",
        '<div class="filters" role="group" '
        f'aria-label="{html.escape(_("Filter findings"))}">',
        f"<strong>{html.escape(_('Filter:'))}</strong> ",
        _filter_checkbox("all", _("All"), checked=True),
        _filter_checkbox("needs", _("Needs attention"), checked=True),
        _filter_checkbox("decorative", _("Decorative review"), checked=True),
        _filter_checkbox("ok", _("Likely OK"), checked=True),
        _filter_checkbox("uncertain", _("Uncertain"), checked=True),
        "</div>",
        '<div class="cards">',
    ]

    for a in sorted_a:
        im = by_index.get(a.index)
        bucket = _filter_bucket(a)
        heur_flags = ""
        if a.index in heur_by:
            heur_flags = ", ".join(heur_by[a.index].flags)
        img_html = _clickable_thumb_html(
            im.image_path if im else None, full_preview=full_preview
        )
        alt_preview = ""
        if im and im.alt_stripped:
            preview = im.alt_stripped
            if len(preview) > 160:
                preview = preview[:157] + "…"
            alt_preview = (
                f'<p class="alt"><strong>{html.escape(_("Alt:"))}</strong> '
                f"{html.escape(preview)}</p>"
            )
        elif im and im.is_decorative:
            alt_preview = f'<p class="alt muted">{html.escape(_("(decorative — no alt)"))}</p>'

        teaching_html = (
            f'<p class="teaching">{html.escape(a.teaching_note)}</p>'
            if a.teaching_note
            else ""
        )
        heur_label = _("Heuristics:")
        flags_html = (
            f'<p class="flags"><strong>{html.escape(heur_label)}</strong> '
            f"{html.escape(heur_flags)}</p>"
            if heur_flags
            else ""
        )
        cards.append(
            f'<article class="card" data-filter="{bucket}">'
            f'<div class="thumb">{img_html}</div>'
            f'<div class="body">'
            f'<p class="title"><span class="badge {_verdict_class(a.verdict)}">'
            f"{html.escape(_verdict_label(a.verdict))}</span> "
            f"#{a.index} — {html.escape(a.filename)}</p>"
            f'<p class="reason">{html.escape(a.reason or "—")}</p>'
            f"{teaching_html}"
            f"{alt_preview}"
            f"{flags_html}"
            f"</div></article>"
        )

    # Heuristic-only images not vision-reviewed
    reviewed = {a.index for a in assessments}
    if heuristics:
        extra = [f for f in heuristics.findings if f.index not in reviewed]
        if extra:
            cards.append(
                f'<p class="note">{html.escape(_("Not AI-reviewed (heuristics only):"))}</p>'
            )
            for finding in extra[:80]:
                im = by_index.get(finding.index)
                flags = ", ".join(finding.flags)
                cards.append(
                    f'<article class="card heuristics-only" data-filter="heuristics">'
                    f'<div class="body">'
                    f'<p class="title"><span class="badge uncertain">'
                    f'{html.escape(_("Not AI-reviewed"))}</span> '
                    f"#{finding.index} — {html.escape(finding.filename)}</p>"
                    f'<p class="flags"><strong>{html.escape(_("Heuristics:"))}</strong> '
                    f"{html.escape(flags)}</p>"
                    f"{_status_line(im)}"
                    f"</div></article>"
                )

    cards.append("</div></section>")
    return "\n".join(cards)


def _status_line(im: AltExportImage | None) -> str:
    if im is None:
        return ""
    status = (im.status or "").strip()
    classification = (im.classification or "").strip()
    unclassified = _("Unclassified")
    if (
        not classification
        or classification.lower() == "unclassified"
        or classification == unclassified
    ):
        classification = ""
    if status and classification:
        text = f"{status} — {classification}"
    else:
        text = status or classification
    if not text:
        return ""
    return f'<p class="alt muted">{html.escape(text)}</p>'


def _filter_checkbox(key: str, label: str, *, checked: bool) -> str:
    chk = " checked" if checked else ""
    return (
        f'<label><input type="checkbox" class="flt" data-key="{key}"{chk}> '
        f"{html.escape(label)}</label> "
    )


_FILTER_JS = """
<script>
(function () {
  function apply() {
    var boxes = document.querySelectorAll('input.flt');
    var on = {};
    boxes.forEach(function (b) { on[b.getAttribute('data-key')] = b.checked; });
    var showAll = on.all;
    document.querySelectorAll('article.card').forEach(function (card) {
      var key = card.getAttribute('data-filter') || '';
      var show = showAll || on[key];
      card.style.display = show ? '' : 'none';
    });
  }
  document.querySelectorAll('input.flt').forEach(function (b) {
    b.addEventListener('change', function () {
      if (b.getAttribute('data-key') === 'all' && b.checked) {
        document.querySelectorAll('input.flt').forEach(function (x) {
          if (x !== b) x.checked = true;
        });
      }
      apply();
    });
  });
})();
function openModal(img) {
  var modal = document.getElementById('imageModal');
  var modalImg = document.getElementById('modalImage');
  if (!modal || !modalImg || !img) return;
  modalImg.src = img.getAttribute('data-full-src') || img.src;
  modal.style.display = 'block';
}
function closeModal() {
  var modal = document.getElementById('imageModal');
  if (modal) modal.style.display = 'none';
}
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  var modal = document.getElementById('imageModal');
  if (modal && modal.style.display === 'block') {
    e.preventDefault();
    closeModal();
  }
});
</script>
"""

_IMAGE_MODAL_HTML = """
<div id="imageModal" class="modal" onclick="closeModal()" role="dialog" aria-modal="true">
  <span class="modal-close" onclick="closeModal()" aria-label="Close">&times;</span>
  <img class="modal-content" id="modalImage" alt="" onclick="event.stopPropagation()">
</div>
"""

_ALT_ASSESS_EXTRA_CSS = """
    /* Wider than overview/explain so thumbnail cards breathe. */
    main.alt-assess {
      max-width: 54rem;
    }
    .doc-header .meta {
      margin: 0.35rem 0 0;
      color: var(--muted);
      font-size: 0.95rem;
      font-weight: 500;
    }
    .doc-header .meta strong {
      color: var(--ink);
      font-weight: 700;
    }
    .stat-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.65rem;
      margin: 0 0 1.25rem;
    }
    .stat-box {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 0.55rem 0.85rem;
      min-width: 5.5rem;
    }
    .stat-box .number {
      font-size: 1.35rem;
      font-weight: 700;
      color: var(--ink);
    }
    .stat-box .label {
      font-size: 0.78rem;
      color: var(--muted);
    }
    .stat-box.needs .number { color: #b42318; }
    .stat-box.ok .number { color: #067647; }
    @media (prefers-color-scheme: dark) {
      .stat-box.needs .number { color: #fca5a5; }
      .stat-box.ok .number { color: #86efac; }
    }
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 0.85rem;
      align-items: center;
      margin: 0.5rem 0 1rem;
      font-size: 0.92rem;
    }
    .cards {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .card {
      display: flex;
      gap: 0.85rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 0.75rem;
    }
    .thumb img, .no-thumb {
      width: 120px;
      height: 90px;
      object-fit: cover;
      border-radius: 0.35rem;
      background: var(--line);
      display: block;
    }
    .thumb img { cursor: pointer; }
    .modal {
      display: none;
      position: fixed;
      z-index: 99;
      inset: 0;
      background: rgba(0,0,0,.85);
    }
    .modal-content {
      max-width: 90%;
      max-height: 90%;
      margin: 5vh auto;
      display: block;
      object-fit: contain;
    }
    .modal-close {
      position: absolute;
      top: 16px;
      right: 28px;
      color: #fff;
      font-size: 2em;
      cursor: pointer;
      line-height: 1;
    }
    .body { flex: 1; min-width: 0; }
    .title { margin: 0 0 0.35rem; font-weight: 600; }
    .reason, .teaching, .alt, .flags, .note { margin: 0.25rem 0; }
    .teaching { color: var(--muted); font-size: 0.92rem; }
    .muted { color: var(--muted); }
    .badge {
      display: inline-block;
      padding: 0.1rem 0.45rem;
      border-radius: 0.3rem;
      font-size: 0.78rem;
      font-weight: 700;
      color: #fff;
    }
    .badge.needs { background: #b42318; }
    .badge.ok { background: #067647; }
    .badge.caveat { background: #b54708; }
    .badge.uncertain { background: #475467; }
    .synthesis { margin-top: 0.25rem; }
    .synthesis > h2:first-child,
    .synthesis > aside.ai-note + h2 {
      margin-top: 0.75rem;
    }
    .priority { margin-top: 1.5rem; }
"""


def build_assessment_html(result: AltAssessResult, *, for_dialog: bool = True) -> str:
    """Full HTML page for the assessment dialog or browser."""
    export = result.export
    if export is None:
        return "<html><body><p>No export.</p></body></html>"

    title = _("Alt text health check")
    synth_md = with_ai_disclaimer(result.text or "")
    # Same markdown → HTML path as overview/explain, including aside.ai-note.
    synth_body = _structure_ai_browser_body(
        markdown_to_body_html(synth_md, for_dialog=False)
    )
    lang = html.escape(get_language())
    direction = html.escape(get_text_direction())
    body_attrs = ' tabindex="-1"' if for_dialog else ""
    footer = ""
    if not for_dialog:
        footer = (
            f'<footer class="doc-footer">'
            f'{html.escape(_("Generated by CheckMate"))}</footer>'
        )

    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{lang}" dir="{direction}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_ai_browser_css()}\n{_ALT_ASSESS_EXTRA_CSS}</style>",
        "</head>",
        f"<body{body_attrs}>",
        '<main class="alt-assess">',
        _stats_html(export, result, title=title),
        f'<section class="synthesis" aria-label="{html.escape(_("Assessment summary"))}">',
        synth_body,
        "</section>",
        _priority_cards_html(
            export,
            result.assessments,
            result.heuristics,
            full_preview=not for_dialog,
        ),
        footer,
        "</main>",
        _IMAGE_MODAL_HTML,
        _FILTER_JS,
        _WEBVIEW_TAB_EXIT_SCRIPT if for_dialog else "",
        "</body></html>",
    ]
    return "\n".join(parts)


def assessment_markdown_export(result: AltAssessResult) -> str:
    """Markdown suitable for Save as Markdown (synthesis + compact table)."""
    lines = [with_ai_disclaimer(result.text or "").rstrip(), "", "---", ""]
    lines.append(f"## {_('AI-reviewed images')}")
    lines.append("")
    for a in result.assessments:
        lines.append(
            f"- **#{a.index}** `{a.filename}` — {_verdict_label(a.verdict)}: "
            f"{a.reason or '—'}"
        )
        if a.teaching_note:
            lines.append(f"  - _{a.teaching_note}_")
    return "\n".join(lines) + "\n"
