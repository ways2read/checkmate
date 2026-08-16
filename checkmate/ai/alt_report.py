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
from .alt_labels import feature_title
from .markdown_html import (
    _WEBVIEW_TAB_EXIT_SCRIPT,
    _ai_browser_css,
    _structure_ai_browser_body,
    markdown_to_body_html,
)

logger = logging.getLogger(__name__)

# Data-URI thumbs are a fallback (saved HTML, or when the report is not
# written beside the export). In-app LoadURL uses relative thumbs/ + images/.
# Cache avoids re-encoding on follow-up repaints of the same report.
_THUMB_CACHE: dict[tuple[str, int, int, int], str] = {}
_THUMB_MAX_EDGE = 160
_THUMB_JPEG_QUALITY = 55
# Larger preview for click-to-enlarge (still embedded; file:// blocked in WebView).
_PREVIEW_MAX_EDGE = 800
_PREVIEW_JPEG_QUALITY = 72
_ALT_PREVIEW_MAX = 320


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
    """Embedded data-URI fallback when a file path cannot be used."""
    return _thumb_data_uri(path)


def _posix_rel(path: Path, folder: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return None
    return rel.as_posix()


def _sidecar_thumb_path(path: Path) -> Path | None:
    """``images/foo.png`` → ``thumbs/foo.jpg`` when that sidecar exists."""
    if path.parent.name != "images":
        return None
    candidate = path.parent.parent / "thumbs" / f"{path.stem}.jpg"
    return candidate if candidate.is_file() else None


def _clickable_thumb_html(
    path: Path | None,
    *,
    full_preview: bool = False,
    export_folder: Path | None = None,
) -> str:
    """Card thumbnail that opens a lightbox preview on click.

    When the HTML lives in *export_folder* (in-app WebView ``LoadURL``), use
    relative ``thumbs/`` + ``images/`` files so the lightbox can show the
    full preview without embedding megabytes of data-URIs. Saved / browser
    HTML still embeds a larger JPEG so the file stays portable.
    """
    if path is None or not path.is_file():
        return '<div class="no-thumb"></div>'

    thumb_file = _sidecar_thumb_path(path)
    rel_full = _posix_rel(path, export_folder) if export_folder else None
    rel_thumb = (
        _posix_rel(thumb_file, export_folder)
        if export_folder and thumb_file is not None
        else None
    )

    if full_preview:
        thumb = _thumb_data_uri(thumb_file or path)
        full = (
            _thumb_data_uri(
                path, max_edge=_PREVIEW_MAX_EDGE, jpg_quality=_PREVIEW_JPEG_QUALITY
            )
            or thumb
        )
    elif rel_full:
        thumb = rel_thumb or rel_full
        full = rel_full
    else:
        thumb = _thumb_data_uri(thumb_file or path)
        full = path.resolve().as_uri() if path.is_file() else thumb

    if not thumb:
        return '<div class="no-thumb"></div>'
    return (
        f'<img src="{html.escape(thumb, quote=True)}" '
        f'data-full-src="{html.escape(full or thumb, quote=True)}" '
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


_FORMAT_ISSUES = frozenset(
    {
        "image_of_table",
        "image_of_math",
        "likely_wrong_orientation",
        "low_resolution",
        "joined_images",
    }
)


def _issue_label(code: str) -> str:
    return {
        "placeholder_alt": _("Placeholder alt"),
        "filename_as_alt": _("Filename used as alt"),
        "inaccurate_alt": _("Inaccurate alt"),
        "too_vague": _("Too vague"),
        "too_verbose": _("Too verbose"),
        "missing_text_in_image": _("Text in image missing from alt"),
        "likely_content_marked_decorative": _("Likely content marked decorative"),
        "likely_decorative_with_alt": _("Likely decorative with alt"),
        "duplicate_alt": _("Duplicate alt"),
        "missing_alt": _("Missing alt"),
        "empty_has_alt": _("Empty alt with has-alt status"),
        "image_of_table": _("Image of a table"),
        "image_of_math": _("Image of math / equation"),
        "likely_wrong_orientation": _("Likely wrong orientation"),
        "low_resolution": _("Low resolution"),
        "joined_images": _("Joined / multi-panel image"),
        "repeats_surrounding_text": _("Repeats surrounding text"),
        "wrong_language": _("Alt language does not match publication"),
        "spelling_or_grammar": _("Spelling or grammar"),
        "very_short_alt": _("Very short alt"),
        "class_decorative_mismatch": _("Classification suggests content"),
        "decorative_with_alt": _("Decorative with alt text"),
        "ok": _("OK"),
    }.get(code, code)


_VERDICT_PRIORITY = {
    "needs_attention": 0,
    "uncertain": 1,
    "likely_ok_with_caveat": 2,
    "ok": 3,
}


def _filter_bucket(a: AltImageAssessment) -> str:
    if a.pass_name == "heuristic":
        return "heuristics"
    if any(i in _FORMAT_ISSUES for i in a.issues):
        return "format"
    if a.verdict == "needs_attention":
        if "likely_content_marked_decorative" in a.issues:
            return "decorative"
        return "needs"
    if a.verdict in {"ok", "likely_ok_with_caveat"}:
        return "ok"
    return "uncertain"


def _card_search_text(*parts: object) -> str:
    bits = [str(p).strip() for p in parts if p is not None and str(p).strip()]
    return " ".join(bits).lower()


def _card_data_attrs(
    *,
    bucket: str,
    index: int,
    filename: str,
    priority: int,
    search: str,
) -> str:
    return (
        f'data-filter="{html.escape(bucket, quote=True)}" '
        f'data-index="{int(index)}" '
        f'data-priority="{int(priority)}" '
        f'data-filename="{html.escape((filename or "").lower(), quote=True)}" '
        f'data-search="{html.escape(search, quote=True)}"'
    )


def _result_model_name(result: AltAssessResult) -> str:
    name = (result.model or "").strip()
    if name:
        return name
    session = result.session
    if session is not None:
        return (getattr(session, "model", "") or "").strip()
    return ""


def report_ai_disclaimer_markdown(model: str = "") -> str:
    """Note banner for the inspector report, including the model when known."""
    model = (model or "").strip()
    if model:
        body = _(
            "This report was generated by CheckMate using AI ({model}) and may contain mistakes!"
        ).format(model=model)
    else:
        body = _(
            "This report was generated by CheckMate using AI and may contain mistakes!"
        )
    return f"## {_('Note')}\n\n{body}\n"


def _report_disclaimer_html(result: AltAssessResult) -> str:
    note = _("Note")
    model = _result_model_name(result)
    if model:
        body = _(
            "This report was generated by CheckMate using AI ({model}) and may contain mistakes!"
        ).format(model=model)
    else:
        body = _(
            "This report was generated by CheckMate using AI and may contain mistakes!"
        )
    return (
        f'<aside class="ai-note" role="note">'
        f"<h2>{html.escape(note)}</h2>"
        f"<p>{html.escape(body)}</p>"
        f"</aside>"
    )


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
    use_export_files: bool = False,
) -> str:
    by_index = {im.index: im for im in export.images}
    # Worst first
    sorted_a = sorted(
        assessments,
        key=lambda a: (_VERDICT_PRIORITY.get(a.verdict, 9), a.index),
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
        _filter_checkbox("format", _("Format & presentation"), checked=True),
        _filter_checkbox("ok", _("Likely OK"), checked=True),
        _filter_checkbox("uncertain", _("Uncertain"), checked=True),
        _filter_checkbox("heuristics", _("Not AI-reviewed"), checked=True),
        f'<label for="search-box">{html.escape(_("Search:"))}</label>',
        f'<input type="search" class="search-box" id="search-box" '
        f'placeholder="{html.escape(_("Search findings..."))}" '
        f'aria-label="{html.escape(_("Search findings"))}">',
        f'<label for="sort-box">{html.escape(_("Sort:"))}</label>',
        f'<select class="sort-box" id="sort-box" '
        f'aria-label="{html.escape(_("Sort findings"))}">'
        f'<option value="priority" selected>{html.escape(_("Priority"))}</option>'
        f'<option value="index">{html.escape(_("Image number"))}</option>'
        f'<option value="filename">{html.escape(_("Filename"))}</option>'
        "</select>",
        "</div>",
        '<div class="cards" id="finding-cards">',
    ]

    for a in sorted_a:
        im = by_index.get(a.index)
        bucket = _filter_bucket(a)
        heur_flags = ""
        if a.index in heur_by:
            heur_flags = ", ".join(
                _issue_label(code) for code in heur_by[a.index].flags
            )
        img_html = _clickable_thumb_html(
            im.image_path if im else None,
            full_preview=full_preview,
            export_folder=export.folder if use_export_files else None,
        )
        alt_preview = ""
        if im and im.alt_stripped:
            preview = im.alt_stripped
            if len(preview) > _ALT_PREVIEW_MAX:
                preview = preview[: _ALT_PREVIEW_MAX - 1] + "…"
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
        visible_issues = [c for c in a.issues if c != "ok"]
        issues_html = ""
        if visible_issues:
            chips = ", ".join(_issue_label(c) for c in visible_issues)
            issues_html = (
                f'<p class="flags"><strong>{html.escape(_("AI flags:"))}</strong> '
                f"{html.escape(chips)}</p>"
            )
        heur_label = _("Heuristics:")
        flags_html = (
            f'<p class="flags"><strong>{html.escape(heur_label)}</strong> '
            f"{html.escape(heur_flags)}</p>"
            if heur_flags
            else ""
        )
        search = _card_search_text(
            a.index,
            a.filename,
            _verdict_label(a.verdict),
            a.reason,
            a.teaching_note,
            im.alt_stripped if im else "",
            im.classification if im else "",
            im.status if im else "",
            " ".join(_issue_label(c) for c in visible_issues),
            heur_flags,
        )
        cards.append(
            f'<article class="card" {_card_data_attrs(bucket=bucket, index=a.index, filename=a.filename, priority=_VERDICT_PRIORITY.get(a.verdict, 9), search=search)}>'
            f'<div class="thumb">{img_html}</div>'
            f'<div class="body">'
            f'<p class="title"><span class="badge {_verdict_class(a.verdict)}">'
            f"{html.escape(_verdict_label(a.verdict))}</span> "
            f"#{a.index} — {html.escape(a.filename)}</p>"
            f'<p class="reason">{html.escape(a.reason or "—")}</p>'
            f"{teaching_html}"
            f"{alt_preview}"
            f"{issues_html}"
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
                flags = ", ".join(_issue_label(code) for code in finding.flags)
                search = _card_search_text(
                    finding.index,
                    finding.filename,
                    _("Not AI-reviewed"),
                    flags,
                    im.status if im else "",
                    im.classification if im else "",
                    im.alt_stripped if im else "",
                )
                cards.append(
                    f'<article class="card heuristics-only" '
                    f'{_card_data_attrs(bucket="heuristics", index=finding.index, filename=finding.filename, priority=8, search=search)}>'
                    f'<div class="body">'
                    f'<p class="title"><span class="badge uncertain">'
                    f'{html.escape(_("Not AI-reviewed"))}</span> '
                    f"#{finding.index} — {html.escape(finding.filename)}</p>"
                    f'<p class="flags"><strong>{html.escape(_("Heuristics:"))}</strong> '
                    f"{html.escape(flags)}</p>"
                    f"{_status_line(im)}"
                    f"</div></article>"
                )

    cards.append("</div>")
    cards.append(
        f'<p class="note" id="no-results" hidden>'
        f'{html.escape(_("No matching images."))}</p>'
    )
    cards.append("</section>")
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
  function query() {
    var box = document.getElementById('search-box');
    return box ? String(box.value || '').toLowerCase().trim() : '';
  }
  function apply() {
    var boxes = document.querySelectorAll('input.flt');
    var on = {};
    boxes.forEach(function (b) { on[b.getAttribute('data-key')] = b.checked; });
    var showAll = on.all;
    var q = query();
    var visible = 0;
    document.querySelectorAll('article.card').forEach(function (card) {
      var key = card.getAttribute('data-filter') || '';
      var show = showAll || on[key];
      var hay = card.getAttribute('data-search') || '';
      if (q && hay.indexOf(q) === -1) show = false;
      card.style.display = show ? '' : 'none';
      if (show) visible += 1;
    });
    var none = document.getElementById('no-results');
    if (none) none.hidden = visible !== 0;
  }
  function sortCards() {
    var sel = document.getElementById('sort-box');
    var wrap = document.getElementById('finding-cards');
    if (!wrap) return;
    var key = sel ? sel.value : 'priority';
    var cards = Array.prototype.slice.call(wrap.querySelectorAll('article.card'));
    cards.sort(function (a, b) {
      if (key === 'index') {
        return (parseInt(a.getAttribute('data-index'), 10) || 0) -
          (parseInt(b.getAttribute('data-index'), 10) || 0);
      }
      if (key === 'filename') {
        return (a.getAttribute('data-filename') || '').localeCompare(
          b.getAttribute('data-filename') || ''
        );
      }
      var pa = parseInt(a.getAttribute('data-priority'), 10);
      var pb = parseInt(b.getAttribute('data-priority'), 10);
      if (isNaN(pa)) pa = 9;
      if (isNaN(pb)) pb = 9;
      if (pa !== pb) return pa - pb;
      return (parseInt(a.getAttribute('data-index'), 10) || 0) -
        (parseInt(b.getAttribute('data-index'), 10) || 0);
    });
    cards.forEach(function (card) { wrap.appendChild(card); });
  }
  document.querySelectorAll('input.flt').forEach(function (b) {
    b.addEventListener('change', function () {
      var key = b.getAttribute('data-key');
      if (key === 'all' && b.checked) {
        document.querySelectorAll('input.flt').forEach(function (x) {
          if (x !== b) x.checked = true;
        });
      } else if (key !== 'all' && !b.checked) {
        var allBox = document.querySelector('input.flt[data-key="all"]');
        if (allBox) allBox.checked = false;
      } else if (key !== 'all' && b.checked) {
        var rest = Array.prototype.slice.call(
          document.querySelectorAll('input.flt:not([data-key="all"])')
        );
        var allOn = rest.every(function (x) { return x.checked; });
        var allBox = document.querySelector('input.flt[data-key="all"]');
        if (allBox && allOn) allBox.checked = true;
      }
      apply();
    });
  });
  var search = document.getElementById('search-box');
  if (search) search.addEventListener('input', apply);
  var sortBox = document.getElementById('sort-box');
  if (sortBox) sortBox.addEventListener('change', sortCards);
  apply();
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
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 0.85rem;
      align-items: center;
      margin: 0.5rem 0 1rem;
      font-size: 0.92rem;
    }
    .filters .search-box,
    .filters .sort-box {
      font: inherit;
      padding: 0.28rem 0.55rem;
      border: 1px solid var(--line);
      border-radius: 0.35rem;
      background: var(--paper);
      color: var(--ink);
    }
    .filters .search-box {
      min-width: 12rem;
      flex: 1 1 12rem;
    }
    .filters .sort-box {
      min-width: 9rem;
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

    title = feature_title()
    has_synth = bool((result.text or "").strip())
    synth_section = ""
    if has_synth:
        synth_body = _structure_ai_browser_body(
            markdown_to_body_html(result.text or "", for_dialog=False)
        )
        synth_section = (
            f'<section class="synthesis" '
            f'aria-label="{html.escape(_("Assessment summary"))}">'
            f"{synth_body}</section>"
        )
    disclaimer = _report_disclaimer_html(result)
    lang = html.escape(get_language())
    direction = html.escape(get_text_direction())
    body_attrs = ' tabindex="-1"' if for_dialog else ""
    footer = ""
    if not for_dialog:
        footer = (
            f'<footer class="doc-footer">'
            f'{html.escape(_("Generated by CheckMate"))}</footer>'
        )

    try:
        from ..ui_appearance import html_color_scheme, html_root_class, wrap_os_dark_css

        color_scheme = html_color_scheme()
        root_class = html_root_class()
        extra_css = _ALT_ASSESS_EXTRA_CSS + wrap_os_dark_css(
            """
      .stat-box.needs .number { color: #fca5a5; }
      .stat-box.ok .number { color: #86efac; }
"""
        )
    except Exception:
        color_scheme = "light dark"
        root_class = "checkmate-theme-system"
        extra_css = _ALT_ASSESS_EXTRA_CSS + (
            "@media (prefers-color-scheme: dark) {\n"
            "      .stat-box.needs .number { color: #fca5a5; }\n"
            "      .stat-box.ok .number { color: #86efac; }\n"
            "}\n"
        )
    parts = [
        "<!DOCTYPE html>",
        f'<html lang="{lang}" dir="{direction}" class="{html.escape(root_class)}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta name="color-scheme" content="{html.escape(color_scheme)}">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_ai_browser_css()}\n{extra_css}</style>",
        "</head>",
        f"<body{body_attrs}>",
        '<main class="alt-assess">',
        _stats_html(export, result, title=title),
        disclaimer,
        synth_section,
        _priority_cards_html(
            export,
            result.assessments,
            result.heuristics,
            full_preview=not for_dialog,
            use_export_files=for_dialog,
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
    lines: list[str] = [f"# {feature_title()}", ""]
    lines.append(report_ai_disclaimer_markdown(_result_model_name(result)).rstrip())
    lines.append("")
    if (result.text or "").strip():
        lines.append((result.text or "").rstrip())
        lines.extend(["", "---", ""])
    lines.append(f"## {_('AI-reviewed images')}")
    lines.append("")
    for a in result.assessments:
        lines.append(
            f"- **#{a.index}** `{a.filename}` — {_verdict_label(a.verdict)}: "
            f"{a.reason or '—'}"
        )
        visible = [c for c in a.issues if c != "ok"]
        if visible:
            labels = ", ".join(_issue_label(c) for c in visible)
            lines.append(f"  - {_('AI flags:')} {labels}")
        if a.teaching_note:
            lines.append(f"  - _{a.teaching_note}_")
    return "\n".join(lines) + "\n"
