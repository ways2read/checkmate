"""Export check results as text or HTML reports."""

from __future__ import annotations

import html
import json
from pathlib import Path

from . import __version__
from .cover_image import CoverImage, extract_cover_image
from .i18n import _, get_language, get_text_direction, ngettext
from .models import CheckResult, Issue, Severity, Verdict
from .updater import EBRAILLE_TOOL, EPUBCHECK_TOOL, VERAPDF_TOOL


def report_title(result: CheckResult) -> str:
    """Human title for text/HTML reports based on which checker ran."""
    name = (result.tool_name or "").strip()
    key = name.lower()
    if "epubcheck" in key and "ace" in key:
        return _("EPUBCheck + Ace report")
    if "nu html" in key and "axe" in key:
        return _("Nu HTML Checker + axe report")
    if "nu html" in key:
        return _("Nu HTML Checker report")
    if key == EPUBCHECK_TOOL.display_name.lower() or "epubcheck" in key:
        return _("EPUBCheck report")
    if key == EBRAILLE_TOOL.display_name.lower() or "ebraille" in key:
        return _("eBraille Checker report")
    if key == VERAPDF_TOOL.display_name.lower() or "verapdf" in key:
        return _("veraPDF report")
    if key == "ace" or key.startswith("ace "):
        return _("Ace report")
    if key == "axe" or key.startswith("axe "):
        return _("axe report")
    return _("Check report")


def format_text_report(result: CheckResult, *, include_full_log: bool = True) -> str:
    lines: list[str] = [report_title(result), ""]
    meta = result.report_meta_lines()
    if meta:
        lines.extend(meta)
        lines.append("")
    lines.append(result.headline)
    if result.issues:
        lines.append("")
        for issue in result.issues:
            lines.append(issue.summary_line())
    body = "\n".join(lines).strip() + "\n"
    if include_full_log and result.raw_log:
        body += "\n" + _("--- Full log ---") + "\n" + result.raw_log
        if not body.endswith("\n"):
            body += "\n"
    return body


def _verdict_class(verdict: Verdict) -> str:
    return {
        Verdict.PASSED: "passed",
        Verdict.PASSED_WITH_WARNINGS: "passed-warnings",
        Verdict.FAILED: "failed",
        Verdict.ERROR: "error",
    }.get(verdict, "error")


def _severity_class(severity: Severity) -> str:
    return {
        Severity.FATAL: "fatal",
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "info",
        Severity.USAGE: "usage",
        Severity.UNKNOWN: "unknown",
    }.get(severity, "unknown")


def _result_source_names(result: CheckResult) -> list[str]:
    """Checker names present in a result, for the Source filter."""
    if result.source_counts:
        return [name for name, _ in result.source_counts if name]
    seen: list[str] = []
    for issue in result.issues:
        if issue.source and issue.source not in seen:
            seen.append(issue.source)
    if seen:
        return seen
    name = (result.tool_name or "").strip()
    if name and " + " not in name:
        return [name]
    return []


def _count_badges_html(result: CheckResult, esc) -> str:
    """Severity count chips for the summary strip."""
    items: list[tuple[str, int, str]] = [
        ("fatal", result.fatals, ngettext("{n} fatal", "{n} fatals", result.fatals)),
        ("error", result.errors, ngettext("{n} error", "{n} errors", result.errors)),
        (
            "warning",
            result.warnings,
            ngettext("{n} warning", "{n} warnings", result.warnings),
        ),
        ("info", result.infos, ngettext("{n} info", "{n} infos", result.infos)),
        ("usage", result.usages, ngettext("{n} usage", "{n} usages", result.usages)),
    ]
    chips = []
    for sev, count, label in items:
        if count <= 0:
            continue
        chips.append(
            f'<li class="count count-{sev}">'
            f'<span class="count-label">{esc(label)}</span>'
            f"</li>"
        )
    if not chips:
        return ""
    return (
        f'<ul class="counts" aria-label="{esc(_("Issue counts"))}">'
        + "".join(chips)
        + "</ul>"
    )


def _filters_html(result: CheckResult, sources: list[str], esc) -> str:
    """Toolbar mirroring the GUI filters, plus text search."""
    if not result.issues:
        return ""

    sev_options = [
        ("all", _("All issues")),
        ("errors", _("Errors only")),
        ("warnings", _("Warnings only")),
        ("info", _("Info / usage")),
    ]
    sev_html = "\n".join(
        f'<option value="{value}">{esc(label)}</option>'
        for value, label in sev_options
    )

    source_block = ""
    if len(sources) >= 2:
        names_lower = {s.lower() for s in sources}
        if names_lower >= {"epubcheck", "ace"}:
            all_sources_label = _("EPUBCheck + Ace")
        elif "nu html checker" in names_lower and "axe" in names_lower:
            all_sources_label = _("Nu HTML Checker + axe")
        else:
            all_sources_label = _("All sources")
        source_opts = [f'<option value="">{esc(all_sources_label)}</option>']
        source_opts.extend(
            f'<option value="{esc(name)}">{esc(name)}</option>' for name in sources
        )
        source_block = f"""
      <div class="filter-field">
        <label for="filter-source">{esc(_("Source:"))}</label>
        <select id="filter-source" name="source">
          {"".join(source_opts)}
        </select>
      </div>"""

    return f"""
    <div class="toolbar" id="issue-filters" role="search" aria-label="{esc(_("Filter issues"))}">
      <div class="filter-field">
        <label for="filter-severity">{esc(_("Filter:"))}</label>
        <select id="filter-severity" name="severity">
{sev_html}
        </select>
      </div>
{source_block}
      <div class="filter-field filter-search">
        <label for="filter-search">{esc(_("Search"))}</label>
        <input type="search" id="filter-search" name="q"
               autocomplete="off" spellcheck="false"
               placeholder="{esc(_("Search issues"))}"
               aria-controls="issues-body"
               aria-describedby="filter-status" />
      </div>
      <div class="filter-field filter-unique">
        <label class="checkbox-label">
          <input type="checkbox" id="filter-unique" name="unique" />
          <span>{esc(_("Show one example of each issue"))}</span>
        </label>
      </div>
      <p class="filter-status" id="filter-status" aria-live="polite"></p>
      <button type="button" class="btn-clear" id="filter-clear" hidden>
        {esc(_("Clear filters"))}
      </button>
    </div>
    <p class="filter-empty" id="filter-empty" hidden>{esc(_("No matching issues."))}</p>"""


def _issue_rows_html(issues: list[Issue], esc) -> str:
    rows: list[str] = []
    for issue in issues:
        sev = _severity_class(issue.severity)
        source = issue.source or ""
        source_display = source or "—"
        search_bits = " ".join(
            [
                issue.severity.label,
                source,
                issue.code,
                issue.location,
                issue.message,
            ]
        )
        rows.append(
            "<tr"
            f' data-severity="{esc(sev)}"'
            f' data-source="{esc(source)}"'
            f' data-code="{esc(issue.code)}"'
            f' data-search="{esc(search_bits.lower())}"'
            ">"
            f'<td class="col-sev" data-label="{esc(_("Severity"))}">'
            f'<span class="sev sev-{sev}">{esc(issue.severity.label)}</span></td>'
            f'<td class="col-source" data-label="{esc(_("Source"))}">'
            f"{esc(source_display)}</td>"
            f'<td class="col-code" data-label="{esc(_("Code"))}">'
            f'<code class="code-text">{esc(issue.code)}</code></td>'
            f'<td class="col-loc" data-label="{esc(_("Location"))}">'
            f"{esc(issue.location)}</td>"
            f'<td class="col-msg" data-label="{esc(_("Message"))}">'
            f"{esc(issue.message)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


_REPORT_DARK_ROOT_TOKENS = """
      :root {
        --ink: #f1f5f9;
        --muted: #94a3b8;
        --paper: #0f172a;
        --card: #1e293b;
        --line: #334155;
        --line-strong: #64748b;
        --focus: #2dd4bf;
        --focus-ring: #0f766e;
        --passed: #bbf7d0;
        --passed-bg: #14532d;
        --passed-border: #166534;
        --warn: #fed7aa;
        --warn-bg: #7c2d12;
        --warn-border: #c2410c;
        --failed: #fecaca;
        --failed-bg: #7f1d1d;
        --failed-border: #b91c1c;
        --fatal-fg: #fecaca;
        --fatal-bg: #7f1d1d;
        --error-fg: #fecaca;
        --error-bg: #7f1d1d;
        --warning-fg: #fed7aa;
        --warning-bg: #7c2d12;
        --info-fg: #bfdbfe;
        --info-bg: #1e3a8a;
        --usage-fg: #e2e8f0;
        --usage-bg: #334155;
        --toolbar-bg: #1e293b;
        --shadow: 0 1px 3px rgb(0 0 0 / 35%);
      }
"""


def _report_css() -> str:
    try:
        from .ui_appearance import wrap_os_dark_css
    except Exception:
        def wrap_os_dark_css(inner: str) -> str:
            return f"@media (prefers-color-scheme: dark) {{\n{inner}\n}}\n"

    return (
        """
    :root {
      --ink: #0f172a;
      --muted: #475569;
      --paper: #eef5fb;
      --card: #ffffff;
      --line: #c9d8e8;
      --line-strong: #8aa0b8;
      --focus: #0f766e;
      --focus-ring: #5eead4;
      --passed: #14532d;
      --passed-bg: #dcfce7;
      --passed-border: #86efac;
      --warn: #9a3412;
      --warn-bg: #ffedd5;
      --warn-border: #fdba74;
      --failed: #991b1b;
      --failed-bg: #fee2e2;
      --failed-border: #fca5a5;
      --fatal-fg: #7f1d1d;
      --fatal-bg: #fef2f2;
      --error-fg: #991b1b;
      --error-bg: #fef2f2;
      --warning-fg: #9a3412;
      --warning-bg: #fff7ed;
      --info-fg: #1e3a8a;
      --info-bg: #eff6ff;
      --usage-fg: #334155;
      --usage-bg: #e8eef5;
      --toolbar-bg: #f5f9fd;
      --shadow: 0 1px 2px rgb(15 23 42 / 8%);
      --radius: 0.5rem;
      --font: "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
      --mono: ui-monospace, "Cascadia Code", "Consolas", "Liberation Mono", monospace;
    }
"""
        + wrap_os_dark_css(_REPORT_DARK_ROOT_TOKENS)
        + """
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
      *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
      }
    }
    body {
      margin: 0;
      font-family: var(--font);
      color: var(--ink);
      background: var(--paper);
      line-height: 1.5;
      font-size: 1rem;
    }
    .skip-link {
      position: absolute;
      left: 0.75rem;
      top: 0.75rem;
      padding: 0.5rem 0.85rem;
      background: var(--card);
      color: var(--ink);
      border: 2px solid var(--focus);
      border-radius: var(--radius);
      font-weight: 600;
      text-decoration: none;
      z-index: 100;
      clip-path: inset(50%);
      width: 1px;
      height: 1px;
      overflow: hidden;
      white-space: nowrap;
    }
    .skip-link:focus {
      clip-path: none;
      width: auto;
      height: auto;
      overflow: visible;
      white-space: normal;
    }
    :focus-visible {
      outline: 3px solid var(--focus-ring);
      outline-offset: 2px;
    }
    main {
      max-width: 72rem;
      margin: 0 auto;
      padding: 1.5rem clamp(1rem, 3vw, 1.75rem) 2.5rem;
    }
    h1 {
      font-size: clamp(1.35rem, 2.5vw, 1.75rem);
      margin: 0 0 1rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      line-height: 1.25;
    }
    h2 {
      font-size: 1.2rem;
      margin: 0 0 0.85rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .verdict {
      margin: 0;
      padding: 0.95rem 1.1rem;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background: var(--card);
      font-size: 1.05rem;
      font-weight: 650;
      line-height: 1.4;
      box-shadow: var(--shadow);
    }
    .verdict.passed {
      background: var(--passed-bg);
      color: var(--passed);
      border-color: var(--passed-border);
    }
    .verdict.passed-warnings {
      background: var(--warn-bg);
      color: var(--warn);
      border-color: var(--warn-border);
    }
    .verdict.failed, .verdict.error {
      background: var(--failed-bg);
      color: var(--failed);
      border-color: var(--failed-border);
    }
    .summary {
      display: flex;
      flex-wrap: wrap;
      gap: 1.25rem 1.5rem;
      align-items: flex-start;
      margin-top: 1.15rem;
      margin-bottom: 0.35rem;
    }
    .summary-main {
      flex: 1 1 18rem;
      min-width: 0;
    }
    .counts {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      list-style: none;
      margin: 0 0 0.9rem;
      padding: 0;
    }
    .count {
      display: inline-flex;
      align-items: baseline;
      gap: 0.35rem;
      padding: 0.35rem 0.7rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--card);
      font-size: 0.875rem;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }
    .count-fatal { color: var(--fatal-fg); background: var(--fatal-bg); border-color: color-mix(in srgb, var(--fatal-fg) 28%, transparent); }
    .count-error { color: var(--error-fg); background: var(--error-bg); border-color: color-mix(in srgb, var(--error-fg) 28%, transparent); }
    .count-warning { color: var(--warning-fg); background: var(--warning-bg); border-color: color-mix(in srgb, var(--warning-fg) 28%, transparent); }
    .count-info { color: var(--info-fg); background: var(--info-bg); border-color: color-mix(in srgb, var(--info-fg) 28%, transparent); }
    .count-usage { color: var(--usage-fg); background: var(--usage-bg); border-color: color-mix(in srgb, var(--usage-fg) 28%, transparent); }
    .meta {
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .meta th, .meta td {
      text-align: left;
      padding: 0.6rem 0.85rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    .meta tr:last-child th, .meta tr:last-child td { border-bottom: 0; }
    .meta th {
      width: 9.5rem;
      color: var(--muted);
      font-weight: 650;
      background: color-mix(in srgb, var(--paper) 70%, var(--card));
    }
    .cover {
      flex: 0 0 auto;
      margin: 0;
      max-width: 11rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 0.45rem;
      box-shadow: var(--shadow);
    }
    .cover img {
      display: block;
      width: 100%;
      height: auto;
      max-height: 16rem;
      object-fit: contain;
      border-radius: 0.25rem;
      background: color-mix(in srgb, var(--paper) 80%, var(--line));
    }
    .cover figcaption {
      margin-top: 0.4rem;
      text-align: center;
      color: var(--muted);
      font-size: 0.8rem;
    }
    .issues-section {
      margin-top: 1.75rem;
    }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      flex-wrap: wrap;
      gap: 0.65rem 1rem;
      align-items: flex-end;
      margin: 0 0 0.85rem;
      padding: 0.85rem 1rem;
      background: color-mix(in srgb, var(--toolbar-bg) 92%, transparent);
      backdrop-filter: blur(8px);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .filter-field {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      min-width: 0;
    }
    .filter-field label,
    .checkbox-label {
      font-size: 0.8rem;
      font-weight: 650;
      color: var(--muted);
    }
    .checkbox-label {
      display: flex;
      align-items: flex-start;
      gap: 0.45rem;
      max-width: 16rem;
      line-height: 1.35;
      cursor: pointer;
      color: var(--ink);
      font-weight: 500;
    }
    .checkbox-label input {
      margin-top: 0.2rem;
      flex-shrink: 0;
      width: 1.05rem;
      height: 1.05rem;
      accent-color: var(--focus);
    }
    .filter-search {
      flex: 1 1 14rem;
    }
    .filter-unique {
      flex: 1 1 12rem;
      justify-content: flex-end;
    }
    select, input[type="search"] {
      font: inherit;
      color: var(--ink);
      background: var(--card);
      border: 1px solid var(--line-strong);
      border-radius: 0.4rem;
      padding: 0.45rem 0.65rem;
      min-height: 2.35rem;
      width: 100%;
    }
    input[type="search"] {
      min-width: 10rem;
    }
    .filter-status {
      flex: 1 1 100%;
      margin: 0;
      font-size: 0.875rem;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    .btn-clear {
      font: inherit;
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--ink);
      background: var(--card);
      border: 1px solid var(--line-strong);
      border-radius: 0.4rem;
      padding: 0.4rem 0.75rem;
      cursor: pointer;
      min-height: 2.35rem;
    }
    .btn-clear:hover {
      border-color: var(--focus);
    }
    .filter-empty {
      margin: 0 0 0.85rem;
      padding: 0.85rem 1rem;
      border-radius: var(--radius);
      border: 1px dashed var(--line-strong);
      background: var(--card);
      color: var(--muted);
    }
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--card);
      box-shadow: var(--shadow);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }
    table.issues {
      table-layout: fixed;
      min-width: 42rem;
    }
    table.issues col.col-sev { width: 7rem; }
    table.issues col.col-source { width: 7rem; }
    table.issues col.col-code { width: 10rem; }
    table.issues col.col-loc { width: 22%; }
    table.issues col.col-msg { width: auto; }
    thead th {
      text-align: left;
      padding: 0.7rem 0.85rem;
      background: color-mix(in srgb, var(--paper) 70%, var(--card));
      border-bottom: 1px solid var(--line);
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: var(--muted);
    }
    tbody td {
      padding: 0.65rem 0.85rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    tbody tr:last-child td { border-bottom: 0; }
    tbody tr:nth-child(even) {
      background: color-mix(in srgb, var(--paper) 55%, var(--card));
    }
    tbody tr:hover {
      background: color-mix(in srgb, var(--focus) 8%, var(--card));
    }
    table.issues td.col-loc,
    table.issues td.col-msg {
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    code, .code-text {
      font-family: var(--mono);
      font-size: 0.88em;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .sev {
      display: inline-block;
      font-weight: 700;
      font-size: 0.78rem;
      line-height: 1.2;
      padding: 0.22rem 0.55rem;
      border-radius: 999px;
      border: 1px solid transparent;
      letter-spacing: 0.01em;
    }
    .sev-fatal {
      color: var(--fatal-fg);
      background: var(--fatal-bg);
      border-color: color-mix(in srgb, var(--fatal-fg) 30%, transparent);
    }
    .sev-error {
      color: var(--error-fg);
      background: var(--error-bg);
      border-color: color-mix(in srgb, var(--error-fg) 30%, transparent);
    }
    .sev-warning {
      color: var(--warning-fg);
      background: var(--warning-bg);
      border-color: color-mix(in srgb, var(--warning-fg) 30%, transparent);
    }
    .sev-info {
      color: var(--info-fg);
      background: var(--info-bg);
      border-color: color-mix(in srgb, var(--info-fg) 30%, transparent);
    }
    .sev-usage, .sev-unknown {
      color: var(--usage-fg);
      background: var(--usage-bg);
      border-color: color-mix(in srgb, var(--usage-fg) 30%, transparent);
    }
    .log-section {
      margin-top: 1.75rem;
    }
    .log-section details {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--card);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .log-section summary {
      cursor: pointer;
      padding: 0.85rem 1rem;
      font-weight: 700;
      font-size: 1.05rem;
      list-style-position: outside;
      margin-left: 1rem;
    }
    .log-section summary:hover {
      color: var(--focus);
    }
    pre {
      margin: 0;
      padding: 0.95rem 1.1rem;
      overflow: auto;
      background: #0f172a;
      color: #f1f5f9;
      border-top: 1px solid var(--line);
      font-family: var(--mono);
      font-size: 0.82rem;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 28rem;
    }
    footer {
      margin-top: 2rem;
      color: var(--muted);
      font-size: 0.85rem;
    }
    @media (max-width: 720px) {
      main { padding: 1rem 0.85rem 2rem; }
      .cover {
        max-width: 8.5rem;
        margin-inline: auto;
      }
      .toolbar {
        position: static;
        align-items: stretch;
      }
      .filter-field, .filter-search, .filter-unique {
        flex: 1 1 100%;
      }
      .checkbox-label { max-width: none; }
      .meta th { width: 7rem; }
      .table-wrap {
        border: 0;
        background: transparent;
        box-shadow: none;
        overflow: visible;
      }
      table.issues {
        min-width: 0;
        table-layout: auto;
      }
      table.issues thead {
        display: none;
      }
      table.issues tbody {
        display: grid;
        gap: 0.75rem;
      }
      table.issues tr {
        display: grid;
        gap: 0.55rem;
        padding: 0.9rem 1rem;
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
      }
      table.issues tr[hidden] {
        display: none !important;
      }
      table.issues tbody tr:nth-child(even),
      table.issues tbody tr:hover {
        background: var(--card);
      }
      table.issues td {
        display: grid;
        gap: 0.15rem;
        padding: 0;
        border: 0;
      }
      table.issues td::before {
        content: attr(data-label);
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        color: var(--muted);
      }
      table.issues td.col-sev {
        justify-items: start;
      }
    }
    @media print {
      body { background: #fff; color: #000; }
      .skip-link, .toolbar, .btn-clear, .filter-empty { display: none !important; }
      .table-wrap, .meta, .cover, .verdict, .log-section details {
        box-shadow: none;
        break-inside: avoid;
      }
      table.issues { min-width: 0; }
      pre { max-height: none; color: #000; background: #eef5fb; }
      .log-section details[open] summary ~ * ,
      .log-section details summary ~ * { display: block !important; }
      .log-section details { border-color: #ccc; }
      a { color: inherit; text-decoration: none; }
    }
    """
    )


def _html_root_class() -> str:
    try:
        from .ui_appearance import html_root_class

        return html_root_class()
    except Exception:
        return "checkmate-theme-system"


def _html_color_scheme() -> str:
    try:
        from .ui_appearance import html_color_scheme

        return html_color_scheme()
    except Exception:
        return "light dark"


def _report_js(total: int) -> str:
    """Client-side filter/search/unique-code logic (strings already escaped via JSON)."""
    strings = {
        "showing": _("Showing {visible} of {total}"),
        "codeCount": _("{code} ×{n}"),
    }
    payload = json.dumps({"total": total, "strings": strings}, ensure_ascii=False)
    return f"""
(function () {{
  var cfg = {payload};
  var severity = document.getElementById("filter-severity");
  var source = document.getElementById("filter-source");
  var search = document.getElementById("filter-search");
  var unique = document.getElementById("filter-unique");
  var status = document.getElementById("filter-status");
  var empty = document.getElementById("filter-empty");
  var clearBtn = document.getElementById("filter-clear");
  var tbody = document.getElementById("issues-body");
  if (!severity || !search || !unique || !tbody) return;

  var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));

  function setCode(row, count) {{
    var codeEl = row.querySelector(".code-text");
    if (!codeEl) return;
    var code = row.getAttribute("data-code") || "";
    if (count > 1) {{
      codeEl.textContent = cfg.strings.codeCount
        .replace(/\\{{code\\}}/g, code)
        .replace(/\\{{n\\}}/g, String(count));
    }} else {{
      codeEl.textContent = code;
    }}
  }}

  function matchesSeverity(row) {{
    var sev = row.getAttribute("data-severity") || "";
    var mode = severity.value;
    if (mode === "errors") return sev === "fatal" || sev === "error";
    if (mode === "warnings") return sev === "warning";
    if (mode === "info") return sev === "info" || sev === "usage";
    return true;
  }}

  function matchesSource(row) {{
    if (!source || !source.value) return true;
    return (row.getAttribute("data-source") || "") === source.value;
  }}

  function matchesSearch(row) {{
    var q = (search.value || "").trim().toLowerCase();
    if (!q) return true;
    var hay = row.getAttribute("data-search") || "";
    return hay.indexOf(q) !== -1;
  }}

  function filtersActive() {{
    if (severity.value !== "all") return true;
    if (source && source.value) return true;
    if ((search.value || "").trim()) return true;
    if (unique.checked) return true;
    return false;
  }}

  function applyFilters() {{
    var matching = [];
    for (var i = 0; i < rows.length; i++) {{
      var row = rows[i];
      var ok = matchesSeverity(row) && matchesSource(row) && matchesSearch(row);
      row.hidden = !ok;
      if (ok) matching.push(row);
    }}

    var visible = 0;
    if (unique.checked) {{
      var groups = Object.create(null);
      var order = [];
      for (var j = 0; j < matching.length; j++) {{
        var r = matching[j];
        var key = (r.getAttribute("data-source") || "") + "\\0" + (r.getAttribute("data-code") || "");
        if (!groups[key]) {{
          groups[key] = {{ row: r, count: 1 }};
          order.push(key);
        }} else {{
          groups[key].count += 1;
          r.hidden = true;
          setCode(r, 1);
        }}
      }}
      for (var k = 0; k < order.length; k++) {{
        var g = groups[order[k]];
        g.row.hidden = false;
        setCode(g.row, g.count);
      }}
      visible = order.length;
    }} else {{
      for (var m = 0; m < matching.length; m++) {{
        matching[m].hidden = false;
        setCode(matching[m], 1);
      }}
      visible = matching.length;
    }}

    if (status) {{
      status.textContent = cfg.strings.showing
        .replace(/\\{{visible\\}}/g, String(visible))
        .replace(/\\{{total\\}}/g, String(cfg.total));
    }}
    if (empty) {{
      empty.hidden = visible !== 0;
    }}
    if (clearBtn) {{
      clearBtn.hidden = !filtersActive();
    }}
  }}

  function clearFilters() {{
    severity.value = "all";
    if (source) source.value = "";
    search.value = "";
    unique.checked = false;
    applyFilters();
    search.focus();
  }}

  severity.addEventListener("change", applyFilters);
  if (source) source.addEventListener("change", applyFilters);
  search.addEventListener("input", applyFilters);
  unique.addEventListener("change", applyFilters);
  if (clearBtn) clearBtn.addEventListener("click", clearFilters);

  document.addEventListener("keydown", function (ev) {{
    if (ev.key === "Escape" && document.activeElement === search && search.value) {{
      search.value = "";
      applyFilters();
      ev.preventDefault();
    }}
  }});

  applyFilters();
}})();
"""


def format_html_report(
    result: CheckResult,
    *,
    include_full_log: bool = True,
    cover: CoverImage | None = None,
) -> str:
    """Build a self-contained HTML report with a results table."""
    esc = html.escape
    title = report_title(result)
    if cover is None and result.target_path:
        cover = extract_cover_image(result.target_path)
    meta_rows = []
    if result.target_path:
        meta_rows.append(
            (
                _("Web page") if result.html_pages else _("Publication"),
                esc(result.target_path),
            )
        )
    if result.tool_name:
        checker = result.tool_name
        if result.tool_version:
            checker = f"{result.tool_name} {result.tool_version}"
        meta_rows.append((_("Checker"), esc(checker)))
    if result.checked_at is not None:
        meta_rows.append(
            (
                _("Date"),
                esc(result.checked_at.strftime("%Y-%m-%d %H:%M:%S")),
            )
        )
    for label, value in result.extra_meta:
        if value:
            meta_rows.append((_(label), esc(value)))
    meta_rows.append((_("GUI version"), esc(__version__)))

    meta_html = "\n".join(
        f'<tr><th scope="row">{esc(label)}</th><td>{value}</td></tr>'
        for label, value in meta_rows
    )

    if cover is not None:
        caption = _("First page") if cover.alt == "First page" else _("Cover")
        cover_html = (
            f'<figure class="cover">'
            f'<img src="{cover.data_uri()}" alt="{esc(caption)}" />'
            f"<figcaption>{esc(caption)}</figcaption>"
            f"</figure>"
        )
    else:
        cover_html = ""

    counts_html = _count_badges_html(result, esc)
    sources = _result_source_names(result)
    filters_html = _filters_html(result, sources, esc)

    if result.issues:
        issues_body = _issue_rows_html(result.issues, esc)
        issues_section = f"""
    <section class="issues-section" id="issues" aria-labelledby="issues-heading">
      <h2 id="issues-heading">{esc(_("Issues"))}</h2>
{filters_html}
      <div class="table-wrap">
        <table class="issues">
          <colgroup>
            <col class="col-sev" />
            <col class="col-source" />
            <col class="col-code" />
            <col class="col-loc" />
            <col class="col-msg" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">{esc(_("Severity"))}</th>
              <th scope="col">{esc(_("Source"))}</th>
              <th scope="col">{esc(_("Code"))}</th>
              <th scope="col">{esc(_("Location"))}</th>
              <th scope="col">{esc(_("Message"))}</th>
            </tr>
          </thead>
          <tbody id="issues-body">
{issues_body}
          </tbody>
        </table>
      </div>
    </section>"""
        script = f"<script>\n{_report_js(len(result.issues))}\n</script>"
    else:
        issues_section = f"""
    <section class="issues-section" id="issues" aria-labelledby="issues-heading">
      <h2 id="issues-heading">{esc(_("Issues"))}</h2>
      <p>{esc(_("No issues listed."))}</p>
    </section>"""
        script = ""

    log_section = ""
    if include_full_log and result.raw_log.strip():
        log_section = f"""
    <section class="log-section" aria-labelledby="log-heading">
      <details>
        <summary id="log-heading">{esc(_("Full checker log"))}</summary>
        <pre>{esc(result.raw_log)}</pre>
      </details>
    </section>"""

    vclass = _verdict_class(result.verdict)
    headline_lines = "<br>\n".join(esc(line) for line in result.result_lines)
    skip = esc(_("Skip to issues"))

    return f"""<!DOCTYPE html>
<html lang="{esc(get_language())}" dir="{esc(get_text_direction())}" class="{esc(_html_root_class())}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="{esc(_html_color_scheme())}">
  <title>{esc(title)}</title>
  <style>{_report_css()}
  </style>
</head>
<body>
  <a class="skip-link" href="#issues">{skip}</a>
  <main>
    <h1>{esc(title)}</h1>
    <p class="verdict {vclass}" role="status">{headline_lines}</p>
    <div class="summary">
      <div class="summary-main">
{counts_html}
        <table class="meta">
          <tbody>
{meta_html}
          </tbody>
        </table>
      </div>
{cover_html}
    </div>
{issues_section}
{log_section}
    <footer>{esc(_("Generated by CheckMate"))}</footer>
  </main>
{script}
</body>
</html>
"""


def save_report(
    path: Path,
    result: CheckResult,
    *,
    fmt: str | None = None,
    include_full_log: bool = True,
) -> None:
    """Write a text or HTML report.

    ``fmt`` may be ``\"html\"`` or ``\"text\"``; when omitted, the destination
    suffix decides.
    """
    path = path.expanduser()
    suffix = path.suffix.lower()
    use_html = fmt == "html" or (fmt is None and suffix in {".html", ".htm"})
    if use_html:
        content = format_html_report(result, include_full_log=include_full_log)
    else:
        content = format_text_report(result, include_full_log=include_full_log)
    path.write_text(content, encoding="utf-8")
