"""Run eBraille Checker and parse results."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .java_util import JavaInfo, cached_java, detect_java, has_bundled_java
from .models import CheckResult, Issue, Severity, Verdict, SEVERITY_ORDER
from .paths import checker_uses_bundled_copy, find_checker_jar
from .subprocess_util import hidden_run_kwargs
from .updater import ensure_checker_installed, read_effective_version


PACKAGED_SUFFIXES = {".ebrl", ".zip"}


def is_packaged_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in PACKAGED_SUFFIXES


def is_exploded_path(path: Path) -> bool:
    return path.is_dir()


def build_command(
    java: JavaInfo,
    jar: Path,
    target: Path,
    *,
    exploded: bool | None = None,
) -> list[str]:
    if exploded is None:
        exploded = is_exploded_path(target)
    cmd = [
        java.path,
        "-Xss4m",
        "-jar",
        str(jar),
        "--profile",
        "ebraille",
    ]
    if exploded:
        cmd.extend(["-mode", "exp"])
    # Write JSON report to a temp file; keep human log on stdout/stderr
    cmd.extend(["--json", "-"])
    cmd.append(str(target))
    return cmd


def _location_from_message(msg: dict) -> str:
    locations = msg.get("locations") or msg.get("Locations") or []
    if not locations and "path" in msg:
        return str(msg.get("path") or "")
    if not locations:
        # Flat EPUBCheck-style fields
        path = msg.get("file") or msg.get("File") or ""
        line = msg.get("line") or msg.get("Line")
        column = msg.get("column") or msg.get("Column")
        if path and line not in (None, -1) and column not in (None, -1):
            return f"{path} ({line},{column})"
        return str(path) if path else ""

    loc = locations[0]
    if isinstance(loc, str):
        return loc
    path = loc.get("path") or loc.get("file") or ""
    if not path:
        url = loc.get("url")
        if isinstance(url, str):
            path = url
        elif isinstance(url, dict):
            path = str(url.get("path") or "")
    line = loc.get("line")
    column = loc.get("column")
    if path and line not in (None, -1) and column not in (None, -1):
        return f"{path} ({line},{column})"
    return str(path) if path else ""


def _issues_from_json(data: dict) -> list[Issue]:
    issues: list[Issue] = []
    messages = data.get("messages") or data.get("Messages") or []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        severity = Severity.from_string(
            msg.get("severity") or msg.get("Severity") or msg.get("type")
        )
        code = str(msg.get("ID") or msg.get("id") or msg.get("code") or "")
        message = str(
            msg.get("message") or msg.get("Message") or msg.get("text") or ""
        ).strip()
        issues.append(
            Issue(
                severity=severity,
                code=code,
                message=message,
                location=_location_from_message(msg),
            )
        )
    issues.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i.severity, 99),
            i.location,
            i.code,
            i.message,
        )
    )
    return issues


def _counts_from_json(data: dict, issues: list[Issue]) -> dict[str, int]:
    def pick(block: dict, *keys: str) -> int | None:
        for key in keys:
            if key in block and block[key] is not None:
                try:
                    return int(block[key])
                except (TypeError, ValueError):
                    continue
        return None

    # Prefer checker summary if present
    for key in ("checker", "Checker", "counts"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        mapped = {
            "fatals": pick(block, "nFatal", "fatal", "fatals"),
            "errors": pick(block, "nError", "error", "errors"),
            "warnings": pick(block, "nWarning", "warning", "warnings"),
            "infos": pick(block, "nInfo", "info", "infos"),
            "usages": pick(block, "nUsage", "usage", "usages"),
        }
        if any(v is not None for v in mapped.values()):
            return {
                "fatals": mapped["fatals"] or 0,
                "errors": mapped["errors"] or 0,
                "warnings": mapped["warnings"] or 0,
                "infos": mapped["infos"] or 0,
                "usages": mapped["usages"] or 0,
            }

    counts = {
        "fatals": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "usages": 0,
    }
    for issue in issues:
        if issue.severity == Severity.FATAL:
            counts["fatals"] += 1
        elif issue.severity == Severity.ERROR:
            counts["errors"] += 1
        elif issue.severity == Severity.WARNING:
            counts["warnings"] += 1
        elif issue.severity == Severity.INFO:
            counts["infos"] += 1
        elif issue.severity == Severity.USAGE:
            counts["usages"] += 1
    return counts


def _verdict_from_counts(counts: dict[str, int], exit_code: int) -> Verdict:
    if counts["fatals"] or counts["errors"]:
        return Verdict.FAILED
    if exit_code not in (0, None) and not (
        counts["warnings"] or counts["infos"] or counts["usages"]
    ):
        # Non-zero exit with no parsed messages (e.g. JVM crash)
        return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    if exit_code not in (0, None):
        return Verdict.FAILED
    return Verdict.PASSED


def _extract_json_object(text: str) -> dict | None:
    """Find the first top-level JSON object in mixed stdout."""
    text = text.strip()
    if not text:
        return None
    # Fast path: whole output is JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    while start != -1:
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(text[start:])
            if isinstance(data, dict) and (
                "messages" in data
                or "Messages" in data
                or "checker" in data
                or "publication" in data
            ):
                return data
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def parse_checker_output(stdout: str, stderr: str, exit_code: int) -> CheckResult:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    data = _extract_json_object(stdout) or _extract_json_object(stderr) or _extract_json_object(
        combined
    )

    if data is None:
        # Fall back to exit code only
        verdict = Verdict.PASSED if exit_code == 0 else Verdict.FAILED
        return CheckResult(
            verdict=verdict,
            raw_log=combined,
            exit_code=exit_code,
            error_message=""
            if exit_code == 0
            else "Could not parse structured results; see the full log.",
        )

    issues = _issues_from_json(data)
    counts = _counts_from_json(data, issues)
    verdict = _verdict_from_counts(counts, exit_code)
    return CheckResult(
        verdict=verdict,
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log=combined,
        exit_code=exit_code,
    )


def run_check(
    target: Path,
    *,
    exploded: bool | None = None,
    progress=None,
) -> CheckResult:
    target = target.expanduser().resolve()
    if not target.exists():
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"Path not found: {target}",
        )

    java = detect_java()
    if java is None:
        if has_bundled_java():
            message = (
                "The bundled Java runtime could not be started. "
                "On macOS this usually means the app needs to be re-signed "
                "with JVM entitlements (allow-jit). Reinstall from a current "
                "notarized build, or install a system JRE 17+."
            )
        else:
            message = (
                "Java was not found. Install a Java Runtime (JRE 8 or newer), "
                "or use a packaged build that includes a bundled runtime."
            )
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=message,
        )

    try:
        jar = find_checker_jar()
        if jar is None:
            jar = ensure_checker_installed(progress=progress)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"Could not install eBraille Checker: {exc}",
        )

    if exploded is None:
        if is_packaged_path(target):
            exploded = False
        elif is_exploded_path(target):
            exploded = True
        else:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message=(
                    "Choose a packaged .ebrl file or an exploded publication folder."
                ),
            )

    cmd = build_command(java, jar, target, exploded=exploded)

    # Prefer writing JSON to a temp file for reliable parsing; also capture console
    with tempfile.TemporaryDirectory(prefix="ebraille-gui-") as tmp:
        json_path = Path(tmp) / "report.json"
        # Rebuild with file path instead of stdout JSON to keep console readable
        cmd = [
            java.path,
            "-Xss4m",
            "-jar",
            str(jar),
            "--profile",
            "ebraille",
        ]
        if exploded:
            cmd.extend(["-mode", "exp"])
        cmd.extend(["--json", str(json_path), str(target)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
                **hidden_run_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                verdict=Verdict.ERROR,
                command=cmd,
                error_message="Check timed out after 10 minutes.",
            )
        except OSError as exc:
            return CheckResult(
                verdict=Verdict.ERROR,
                command=cmd,
                error_message=f"Failed to start Java: {exc}",
            )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        raw_log = "\n".join(p for p in (stdout, stderr) if p)

        data = None
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None

        if isinstance(data, dict):
            issues = _issues_from_json(data)
            counts = _counts_from_json(data, issues)
            verdict = _verdict_from_counts(counts, proc.returncode)
            return CheckResult(
                verdict=verdict,
                fatals=counts["fatals"],
                errors=counts["errors"],
                warnings=counts["warnings"],
                infos=counts["infos"],
                usages=counts["usages"],
                issues=issues,
                raw_log=raw_log or json.dumps(data, indent=2),
                exit_code=proc.returncode,
                command=cmd,
            )

        result = parse_checker_output(stdout, stderr, proc.returncode)
        result.command = cmd
        return result


def checker_status_text() -> str:
    from .i18n import _

    version = read_effective_version()
    jar = find_checker_jar()
    java = cached_java()
    parts: list[str] = []
    if version and jar:
        if checker_uses_bundled_copy():
            parts.append(_("Checker {version} (bundled)", version=version))
        else:
            parts.append(_("Checker {version}", version=version))
    elif jar:
        parts.append(_("Checker installed"))
    else:
        parts.append(_("Checker not installed"))
    if java:
        parts.append(java.label)
    else:
        parts.append(_("Java not found"))
    return " · ".join(parts)
