"""Export check results as text or HTML reports."""

from __future__ import annotations

import html
from pathlib import Path

from . import __version__
from .i18n import _, get_language
from .models import CheckResult, Severity, Verdict
from .updater import EBRAILLE_TOOL, EPUBCHECK_TOOL


def report_title(result: CheckResult) -> str:
    """Human title for text/HTML reports based on which checker ran."""
    name = (result.tool_name or "").strip()
    key = name.lower()
    if key == EPUBCHECK_TOOL.display_name.lower() or "epubcheck" in key:
        return _("EPUBCheck report")
    if key == EBRAILLE_TOOL.display_name.lower() or "ebraille" in key:
        return _("eBraille Checker report")
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


def format_html_report(result: CheckResult, *, include_full_log: bool = True) -> str:
    """Build a self-contained HTML report with a results table."""
    esc = html.escape
    title = report_title(result)
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
    meta_rows.append((_("GUI version"), esc(__version__)))

    meta_html = "\n".join(
        f"<tr><th scope=\"row\">{esc(label)}</th><td>{value}</td></tr>"
        for label, value in meta_rows
    )

    issue_rows = []
    for issue in result.issues:
        sev = _severity_class(issue.severity)
        issue_rows.append(
            "<tr>"
            f'<td><span class="sev sev-{sev}">{esc(issue.severity.label)}</span></td>'
            f"<td><code>{esc(issue.code)}</code></td>"
            f"<td>{esc(issue.location)}</td>"
            f"<td>{esc(issue.message)}</td>"
            "</tr>"
        )
    if issue_rows:
        issues_body = "\n".join(issue_rows)
        issues_section = f"""
    <section aria-labelledby="issues-heading">
      <h2 id="issues-heading">{esc(_("Issues"))}</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">{esc(_("Severity"))}</th>
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
      max-width: 1100px;
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
    thead th {{
      text-align: left;
      padding: 0.65rem 0.75rem;
      background: #f5f5f4;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }}
    tbody td {{
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    tbody tr:nth-child(even) {{ background: #fafaf9; }}
    code {{
      font-family: ui-monospace, "Cascadia Code", "Consolas", monospace;
      font-size: 0.9em;
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
  </style>
</head>
<body>
  <main>
    <h1>{esc(title)}</h1>
    <p class="verdict {vclass}" role="status">{headline_lines}</p>
    <table class="meta">
      <tbody>
{meta_html}
      </tbody>
    </table>
{issues_section}
{log_section}
    <footer>{esc(_("Generated by eBraille Checker GUI"))}</footer>
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
