"""Parse DAISY Pipeline daisy202-validator HTML reports into Issue models."""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

from .models import Issue, Severity

_SCH_RE = re.compile(
    r"\[sch\]"
    r"(?:\[(?P<code>[^\]]+)\])?"
    r"(?:\[type::(?P<stype>[^\]]+)\])?"
    r"(?:\[msg::(?P<msg>[^\]]*)\])?",
    re.IGNORECASE,
)


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: list[Issue] = []
        self.info_lines: list[str] = []
        self._li_class: str | None = None
        self._in_li = False
        self._in_p = False
        self._in_pre = False
        self._in_h3 = False
        self._capture_location = False
        self._p_parts: list[str] = []
        self._pre_parts: list[str] = []
        self._pending_message: str = ""
        self._pending_location: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "li":
            self._in_li = True
            self._li_class = ad.get("class") or ""
            self._pending_message = ""
            self._pending_location = ""
            self._p_parts = []
            self._pre_parts = []
            self._capture_location = False
        elif tag == "p" and self._in_li:
            self._in_p = True
            self._p_parts = []
        elif tag == "h3" and self._in_li:
            self._in_h3 = True
        elif tag == "pre" and self._in_li:
            self._in_pre = True
            self._pre_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_p:
            self._in_p = False
            text = html_lib.unescape("".join(self._p_parts)).strip()
            if text and not self._pending_message:
                self._pending_message = text
        elif tag == "h3" and self._in_h3:
            self._in_h3 = False
            # Location heading follows; next <pre> is the location value
            self._capture_location = True
        elif tag == "pre" and self._in_pre:
            self._in_pre = False
            text = html_lib.unescape("".join(self._pre_parts)).strip()
            if self._capture_location and text and not self._pending_location:
                self._pending_location = text
            elif text and not self._pending_location:
                # message-details path
                self._pending_location = text
            self._capture_location = False
        elif tag == "li" and self._in_li:
            self._finish_li()
            self._in_li = False
            self._li_class = None

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._p_parts.append(data)
        elif self._in_pre:
            self._pre_parts.append(data)

    def _finish_li(self) -> None:
        cls = (self._li_class or "").lower()
        msg = self._pending_message.strip()
        loc = self._pending_location.strip()
        if not msg:
            return

        if "message-info" in cls:
            line = msg if not loc else f"{msg} ({loc})"
            self.info_lines.append(line)
            return

        if "message-error" in cls:
            self.issues.append(
                Issue(
                    severity=Severity.ERROR,
                    code="timing",
                    message=msg,
                    location=_short_location(loc),
                )
            )
            return

        if "message-warning" in cls:
            self.issues.append(
                Issue(
                    severity=Severity.WARNING,
                    code="warning",
                    message=msg,
                    location=_short_location(loc),
                )
            )
            return

        # Schematron-style: [sch][dtb::d202][type::warning][msg::...]
        match = _SCH_RE.search(msg)
        if match:
            stype = (match.group("stype") or "error").strip().lower()
            severity = Severity.from_string(stype)
            if severity == Severity.UNKNOWN:
                severity = Severity.ERROR
            code = (match.group("code") or "sch").strip() or "sch"
            body = (match.group("msg") or "").strip() or msg
            self.issues.append(
                Issue(
                    severity=severity,
                    code=code,
                    message=body,
                    location=_short_location(loc),
                )
            )
            return

        if "error" in cls:
            self.issues.append(
                Issue(
                    severity=Severity.ERROR,
                    code="error",
                    message=msg,
                    location=_short_location(loc),
                )
            )


def _short_location(loc: str) -> str:
    if not loc:
        return ""
    # Prefer basename for file: URIs
    if "file:" in loc.lower():
        name = loc.rstrip("/").rsplit("/", 1)[-1]
        try:
            from urllib.parse import unquote

            name = unquote(name)
        except Exception:  # noqa: BLE001
            pass
        return name or loc
    # Compact long XPath a bit for the list column
    if len(loc) > 120:
        return loc[:117] + "…"
    return loc


def parse_daisy202_html_report(html_text: str) -> tuple[list[Issue], list[str]]:
    """Return (issues for the list, info lines for the log)."""
    parser = _ReportParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001 — fall back to empty parse
        return [], []
    return parser.issues, parser.info_lines
