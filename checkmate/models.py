"""Result models for checker runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .i18n import _, ngettext


class Severity(str, Enum):
    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    USAGE = "usage"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, value: str | None) -> Severity:
        if not value:
            return cls.UNKNOWN
        key = value.strip().lower()
        for member in cls:
            if member.value == key:
                return member
        return cls.UNKNOWN

    @property
    def label(self) -> str:
        return {
            Severity.FATAL: _("Fatal"),
            Severity.ERROR: _("Error"),
            Severity.WARNING: _("Warning"),
            Severity.INFO: _("Info"),
            Severity.USAGE: _("Usage"),
            Severity.UNKNOWN: _("Unknown"),
        }[self]


class Verdict(str, Enum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"
    ERROR = "error"  # tool/runtime failure

    @property
    def label(self) -> str:
        return {
            Verdict.PASSED: _("Passed"),
            Verdict.PASSED_WITH_WARNINGS: _("Passed with warnings"),
            Verdict.FAILED: _("Failed"),
            Verdict.ERROR: _("Could not complete check"),
        }[self]


SEVERITY_ORDER = {
    Severity.FATAL: 0,
    Severity.ERROR: 1,
    Severity.WARNING: 2,
    Severity.INFO: 3,
    Severity.USAGE: 4,
    Severity.UNKNOWN: 5,
}


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    location: str = ""
    # Checker that produced this issue (e.g. "EPUBCheck", "Ace", "veraPDF").
    source: str = ""
    # Optional Ace (or other) help link for AI "Learn more" and UI.
    help_url: str = ""
    help_title: str = ""
    # Ace help dct:description (remediation tip); empty when not provided.
    help_text: str = ""
    # Ace earl:impact (critical / serious / moderate / minor); empty for others.
    impact: str = ""
    # Ace ruleset label(s) e.g. "WCAG 2.0 A", "EPUB"; empty when unknown.
    ruleset: str = ""

    def summary_line(self) -> str:
        parts = [self.severity.label]
        if self.source:
            parts.append(self.source)
        parts.append(self.code)
        if self.location:
            parts.append(self.location)
        head = "  ".join(p for p in parts if p)
        return f"{head}: {self.message}" if self.message else head


@dataclass
class CheckResult:
    verdict: Verdict
    fatals: int = 0
    errors: int = 0
    warnings: int = 0
    infos: int = 0
    usages: int = 0
    issues: list[Issue] = field(default_factory=list)
    raw_log: str = ""
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    error_message: str = ""
    tool_name: str = ""
    tool_version: str = ""
    checked_at: datetime | None = None
    target_path: str = ""
    # Optional checker-specific summary rows (label, value), e.g. veraPDF profile.
    extra_meta: list[tuple[str, str]] = field(default_factory=list)
    # Per-tool severity totals for merged runs (e.g. EPUBCheck + Ace):
    # (source name, counts dict with fatals/errors/warnings/infos/usages).
    source_counts: list[tuple[str, dict[str, int]]] = field(default_factory=list)
    # HTML checks: crawled page URLs and rendered-DOM image records for alt export.
    html_pages: list[str] = field(default_factory=list)
    html_images: list[dict] = field(default_factory=list)

    @property
    def headline(self) -> str:
        """Single-line summary for title bar, clipboard, and announcements."""
        return " — ".join(self.result_lines)

    @property
    def result_display(self) -> str:
        """Multi-line text for the result pane (Up/Down line navigation)."""
        return "\n".join(self.result_lines)

    @staticmethod
    def _severity_parts(fatals: int, errors: int, warnings: int) -> list[str]:
        parts: list[str] = []
        if fatals:
            parts.append(ngettext("{n} fatal", "{n} fatals", fatals))
        if errors:
            parts.append(ngettext("{n} error", "{n} errors", errors))
        if warnings:
            parts.append(ngettext("{n} warning", "{n} warnings", warnings))
        return parts

    @property
    def result_lines(self) -> list[str]:
        if self.verdict == Verdict.ERROR:
            if self.error_message:
                # Keep the verdict on its own line; put the detail below.
                msg = self.error_message.strip()
                if msg.lower().startswith(self.verdict.label.lower()):
                    return [msg]
                return [self.verdict.label, msg]
            return [self.verdict.label]

        parts = self._severity_parts(self.fatals, self.errors, self.warnings)

        label = self.verdict.label
        if not parts:
            if self.verdict == Verdict.FAILED:
                lines = [label, _("see the full log for details")]
            else:
                lines = [label, _("no errors or warnings")]
        else:
            lines = [label, ", ".join(parts)]

        # Merged runs (EPUBCheck + Ace): show each tool's share of the totals.
        for source, counts in self.source_counts:
            sub = self._severity_parts(
                counts.get("fatals", 0),
                counts.get("errors", 0),
                counts.get("warnings", 0),
            )
            if sub:
                lines.append(f"{source}: " + ", ".join(sub))
            else:
                lines.append(f"{source}: " + _("no errors or warnings"))

        return lines

    def report_meta_lines(self) -> list[str]:
        """Metadata lines for copy/save reports (publication, checker, date)."""
        lines: list[str] = []
        if self.target_path:
            if self.html_pages:
                lines.append(_("Web page: {path}", path=self.target_path))
            else:
                lines.append(_("Publication: {path}", path=self.target_path))
        if self.tool_name:
            if self.tool_version:
                lines.append(
                    _(
                        "Checker: {name} {version}",
                        name=self.tool_name,
                        version=self.tool_version,
                    )
                )
            else:
                lines.append(_("Checker: {name}", name=self.tool_name))
        if self.checked_at is not None:
            when = self.checked_at.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(_("Date: {when}", when=when))
        for label, value in self.extra_meta:
            if not value:
                continue
            lines.append(f"{_(label)}: {value}")
        return lines

    def announcement(self) -> str:
        return _("Check finished. {headline}.", headline=self.headline)
