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
    markdown_to_body_html,
    with_ai_disclaimer,
)

logger = logging.getLogger(__name__)

# WebView2 SetPage blocks file:// image loads; embed data URIs instead.
# Cache avoids re-encoding on follow-up repaints of the same report.
_THUMB_CACHE: dict[tuple[str, int, int], str] = {}
_THUMB_MAX_EDGE = 160
_THUMB_JPEG_QUALITY = 55


def _thumb_data_uri(path: Path | None, *, max_edge: int = _THUMB_MAX_EDGE) -> str:
    """Small JPEG data-URI suitable for WebView ``SetPage`` (not file://)."""
    if path is None or not path.is_file():
        return ""
    try:
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
    except OSError:
        return ""
    cache_key = (str(resolved), max_edge, mtime_ns)
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
        data = pix.tobytes("jpeg", jpg_quality=_THUMB_JPEG_QUALITY)
        b64 = base64.standard_b64encode(data).decode("ascii")
        uri = f"data:image/jpeg;base64,{b64}"
    except Exception:
        logger.debug("Fast thumb encode failed for %s", resolved, exc_info=True)
        try:
            raw = resolved.read_bytes()
            if len(raw) <= 200_000:
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


def _stats_html(export: AltExport, result: AltAssessResult) -> str:
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
        '<div class="stats">',
        f'<p class="meta"><strong>{html.escape(_("Document:"))}</strong> '
        f"{html.escape(export.document_name)}</p>",
        f'<p class="meta"><strong>{html.escape(_("Mode:"))}</strong> '
        f"{html.escape(mode)} — {reviewed}/{counts['total']}</p>",
    ]
    parts.append('<div class="stat-row">')
    for number, label, klass in boxes:
        cls = f' stat-box {klass}'.strip() if klass else "stat-box"
        parts.append(
            f'<div class="{cls}"><div class="number">{html.escape(number)}</div>'
            f'<div class="label">{html.escape(label)}</div></div>'
        )
    parts.append("</div></div>")
    return "\n".join(parts)


def _priority_cards_html(
    export: AltExport,
    assessments: list[AltImageAssessment],
    heuristics: HeuristicReport | None,
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
        thumb = _thumb_src(im.image_path if im else None)
        bucket = _filter_bucket(a)
        heur_flags = ""
        if a.index in heur_by:
            heur_flags = ", ".join(heur_by[a.index].flags)
        img_html = (
            f'<img src="{html.escape(thumb, quote=True)}" alt="" width="120" '
            f'height="90" loading="lazy">'
            if thumb
            else '<div class="no-thumb"></div>'
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
</script>
"""

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f5;
  --fg: #1a1a1a;
  --card: #fff;
  --border: #d0d0cc;
  --needs: #b42318;
  --ok: #067647;
  --caveat: #b54708;
  --uncertain: #475467;
  --muted: #667085;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161616;
    --fg: #f2f2f2;
    --card: #222;
    --border: #444;
    --muted: #98a2b3;
  }
}
body {
  font-family: "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 1.25rem 1.5rem 2.5rem;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.45;
}
h1 { font-size: 1.45rem; margin: 0 0 0.75rem; }
h2 { font-size: 1.15rem; margin: 1.5rem 0 0.75rem; }
.disclaimer {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin: 0 0 1rem;
  background: var(--card);
  font-size: 0.95rem;
}
.stats .meta { margin: 0.25rem 0; }
.stat-row {
  display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 0.75rem 0 1rem;
}
.stat-box {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.55rem 0.85rem;
  min-width: 5.5rem;
}
.stat-box .number { font-size: 1.35rem; font-weight: 700; }
.stat-box .label { font-size: 0.8rem; color: var(--muted); }
.stat-box.needs .number { color: var(--needs); }
.stat-box.ok .number { color: var(--ok); }
.filters {
  display: flex; flex-wrap: wrap; gap: 0.5rem 0.85rem;
  align-items: center; margin: 0.5rem 0 1rem; font-size: 0.92rem;
}
.cards { display: flex; flex-direction: column; gap: 0.75rem; }
.card {
  display: flex; gap: 0.85rem;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem;
}
.thumb img, .no-thumb {
  width: 120px; height: 90px; object-fit: cover;
  border-radius: 4px; background: #ddd; display: block;
}
.no-thumb { background: var(--border); }
.body { flex: 1; min-width: 0; }
.title { margin: 0 0 0.35rem; font-weight: 600; }
.reason, .teaching, .alt, .flags, .note { margin: 0.25rem 0; }
.teaching { color: var(--muted); font-size: 0.92rem; }
.muted { color: var(--muted); }
.badge {
  display: inline-block; padding: 0.1rem 0.45rem; border-radius: 4px;
  font-size: 0.78rem; font-weight: 700; color: #fff;
}
.badge.needs { background: var(--needs); }
.badge.ok { background: var(--ok); }
.badge.caveat { background: var(--caveat); }
.badge.uncertain { background: var(--uncertain); }
.synthesis { margin-top: 0.5rem; }
.synthesis h2:first-child { margin-top: 0; }
.chat-bubble.chat-user {
  background: #dbeafe;
  color: #0f172a;
  border: 1px solid #93c5fd;
  border-radius: 0.75rem;
  padding: 0.65rem 0.85rem;
  margin: 0.75rem 0;
  max-width: 40rem;
}
.chat-bubble.chat-user .chat-user-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
  opacity: 0.8;
}
.chat-bubble.chat-user p { margin: 0; }
@media (prefers-color-scheme: dark) {
  .chat-bubble.chat-user {
    background: #1e3a5f;
    color: #e2e8f0;
    border-color: #3b82f6;
  }
}
"""


def build_assessment_html(result: AltAssessResult, *, for_dialog: bool = True) -> str:
    """Full HTML page for the assessment dialog or browser."""
    export = result.export
    if export is None:
        return "<html><body><p>No export.</p></body></html>"

    title = _("Alt text health check")
    synth_md = with_ai_disclaimer(result.text or "")
    # Split disclaimer from body for layout: with_ai_disclaimer prepends a block
    synth_body = markdown_to_body_html(synth_md, for_dialog=for_dialog)
    lang = html.escape(get_language())
    direction = html.escape(get_text_direction())

    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{lang}" dir="{direction}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        f"<h1>{html.escape(title)}</h1>",
        _stats_html(export, result),
        f'<section class="synthesis" aria-label="{html.escape(_("Assessment summary"))}">',
        synth_body,
        "</section>",
        _priority_cards_html(export, result.assessments, result.heuristics),
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
