"""Export check results as text or HTML reports."""

from __future__ import annotations

import html
from pathlib import Path

from . import __version__
from .cover_image import CoverImage, extract_cover_image
from .i18n import _, get_language
from .models import CheckResult, Severity, Verdict
from .updater import EBRAILLE_TOOL, EPUBCHECK_TOOL, VERAPDF_TOOL


def report_title(result: CheckResult) -> str:
    """Human title for text/HTML reports based on which checker ran."""
    name = (result.tool_name or "").strip()
    key = name.lower()
    if "epubcheck" in key and "ace" in key:
        return _("EPUBCheck + Ace report")
    if key == EPUBCHECK_TOOL.display_name.lower() or "epubcheck" in key:
        return _("EPUBCheck report")
    if key == EBRAILLE_TOOL.display_name.lower() or "ebraille" in key:
        return _("eBraille Checker report")
    if key == VERAPDF_TOOL.display_name.lower() or "verapdf" in key:
        return _("veraPDF report")
    if key == "ace" or key.startswith("ace "):
        return _("Ace report")
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
                _("Publication"),
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
        f"<tr><th scope=\"row\">{esc(label)}</th><td>{value}</td></tr>"
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

    issue_rows = []
    for issue in result.issues:
        sev = _severity_class(issue.severity)
        issue_rows.append(
            "<tr>"
            f'<td class="col-sev"><span class="sev sev-{sev}">{esc(issue.severity.label)}</span></td>'
            f'<td class="col-source">{esc(issue.source or "—")}</td>'
            f'<td class="col-code"><code>{esc(issue.code)}</code></td>'
            f'<td class="col-loc">{esc(issue.location)}</td>'
            f'<td class="col-msg">{esc(issue.message)}</td>'
            "</tr>"
        )
    if issue_rows:
        issues_body = "\n".join(issue_rows)
        issues_section = f"""
    <section aria-labelledby="issues-heading">
      <h2 id="issues-heading">{esc(_("Issues"))}</h2>
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
          <tbody>
{issues_body}
          </tbody>
        </table>
      </div>
    </section>"""
    else:
        issues_section = f"""
    <section aria-labelledby="issues-heading">
      <h2 id="issues-heading">{esc(_("Issues"))}</h2>
      <p>{esc(_("No issues listed."))}</p>
    </section>"""

    log_section = ""
    if include_full_log and result.raw_log.strip():
        log_section = f"""
    <section aria-labelledby="log-heading">
      <h2 id="log-heading">{esc(_("Full checker log"))}</h2>
      <pre>{esc(result.raw_log)}</pre>
    </section>"""

    vclass = _verdict_class(result.verdict)
    headline_lines = "<br>\n".join(esc(line) for line in result.result_lines)

    return f"""<!DOCTYPE html>
<html lang="{esc(get_language())}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      --ink: #1c1917;
      --muted: #57534e;
      --paper: #fafaf9;
      --card: #ffffff;
      --line: #d6d3d1;
      --passed: #166534;
      --passed-bg: #dcfce7;
      --warn: #9a3412;
      --warn-bg: #ffedd5;
      --failed: #991b1b;
      --failed-bg: #fee2e2;
      --fatal: #7f1d1d;
      --error: #b91c1c;
      --warning: #c2410c;
      --info: #1e3a8a;
      --usage: #334155;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
      color: var(--ink);
      background: var(--paper);
      line-height: 1.45;
    }}
    main {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 1.5rem;
    }}
    h1 {{
      font-size: 1.5rem;
      margin: 0 0 1rem;
      font-weight: 650;
    }}
    h2 {{
      font-size: 1.15rem;
      margin: 1.75rem 0 0.75rem;
      font-weight: 650;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 1.25rem 1.5rem;
      align-items: flex-start;
      margin-top: 1rem;
      margin-bottom: 0.25rem;
    }}
    .summary-main {{
      flex: 1 1 18rem;
      min-width: 0;
    }}
    .summary-main .meta {{
      margin-top: 0;
    }}
    .cover {{
      flex: 0 0 auto;
      margin: 0;
      max-width: 11rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 0.4rem;
      padding: 0.4rem;
      box-shadow: 0 1px 2px rgb(28 25 23 / 6%);
    }}
    .cover img {{
      display: block;
      width: 100%;
      height: auto;
      max-height: 16rem;
      object-fit: contain;
      border-radius: 0.2rem;
      background: #f5f5f4;
    }}
    .cover figcaption {{
      margin-top: 0.35rem;
      text-align: center;
      color: var(--muted);
      font-size: 0.8rem;
    }}
    .verdict {{
      padding: 0.9rem 1rem;
      border-radius: 0.4rem;
      border: 1px solid var(--line);
      background: var(--card);
      font-size: 1.05rem;
      font-weight: 600;
    }}
    .verdict.passed {{ background: var(--passed-bg); color: var(--passed); border-color: #86efac; }}
    .verdict.passed-warnings {{ background: var(--warn-bg); color: var(--warn); border-color: #fdba74; }}
    .verdict.failed, .verdict.error {{ background: var(--failed-bg); color: var(--failed); border-color: #fca5a5; }}
    .meta {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 0.4rem;
      overflow: hidden;
    }}
    .meta th, .meta td {{
      text-align: left;
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    .meta tr:last-child th, .meta tr:last-child td {{ border-bottom: 0; }}
    .meta th {{
      width: 10rem;
      color: var(--muted);
      font-weight: 600;
      background: #f5f5f4;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 0.4rem;
      background: var(--card);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }}
    table.issues {{
      table-layout: fixed;
      min-width: 40rem;
    }}
    table.issues col.col-sev {{ width: 6.5rem; }}
    table.issues col.col-source {{ width: 6.5rem; }}
    table.issues col.col-code {{ width: 9rem; }}
    table.issues col.col-loc {{ width: 22%; }}
    table.issues col.col-msg {{ width: auto; }}
    thead th {{
      text-align: left;
      padding: 0.65rem 0.75rem;
      background: #f5f5f4;
      border-bottom: 1px solid var(--line);
    }}
    tbody td {{
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    tbody tr:nth-child(even) {{ background: #fafaf9; }}
    table.issues td.col-loc,
    table.issues td.col-msg {{
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    table.issues td.col-msg {{
      min-width: 12rem;
    }}
    code {{
      font-family: ui-monospace, "Cascadia Code", "Consolas", monospace;
      font-size: 0.9em;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .sev {{
      display: inline-block;
      font-weight: 650;
      font-size: 0.85rem;
    }}
    .sev-fatal {{ color: var(--fatal); }}
    .sev-error {{ color: var(--error); }}
    .sev-warning {{ color: var(--warning); }}
    .sev-info {{ color: var(--info); }}
    .sev-usage, .sev-unknown {{ color: var(--usage); }}
    pre {{
      margin: 0;
      padding: 0.9rem 1rem;
      overflow: auto;
      background: #1c1917;
      color: #f5f5f4;
      border-radius: 0.4rem;
      font-size: 0.85rem;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    footer {{
      margin-top: 2rem;
      color: var(--muted);
      font-size: 0.85rem;
    }}
    @media (max-width: 720px) {{
      main {{ padding: 1rem; }}
      .cover {{
        max-width: 8.5rem;
        margin-inline: auto;
      }}
      table.issues col.col-sev {{ width: 5.5rem; }}
      table.issues col.col-source {{ width: 5rem; }}
      table.issues col.col-code {{ width: 7rem; }}
      table.issues col.col-loc {{ width: 28%; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{esc(title)}</h1>
    <p class="verdict {vclass}" role="status">{headline_lines}</p>
    <div class="summary">
      <div class="summary-main">
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
