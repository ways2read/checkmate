"""Optional DAISY Pipeline 2 webservice client (secret DAISY 2.02 path)."""

from __future__ import annotations

import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

PIPELINE_NS = "http://www.daisy.org/ns/pipeline/data"
_NS = {"d": PIPELINE_NS}
SCRIPT_ID = "daisy202-validator"
DEFAULT_BASE_URLS = (
    "http://127.0.0.1:8181/ws",
    "http://localhost:8181/ws",
)
PROBE_TIMEOUT = 1.5
STATUS_PROBE_TIMEOUT = 0.4
STATUS_PROBE_CACHE_TTL = 30.0
REQUEST_TIMEOUT = 30
POLL_INTERVAL = 0.5
JOB_TIMEOUT = 600

_PIPELINE_STATUS_CACHE: PipelineStatus | None | bool = False
_PIPELINE_STATUS_CACHED_AT = 0.0


@dataclass(frozen=True)
class PipelineStatus:
    base_url: str
    version: str
    authentication: bool
    localfs: bool


def _local_appdata() -> Path:
    import os

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base)
    # Electron userData on macOS / Linux
    home = Path.home()
    for candidate in (
        home / "Library" / "Application Support",
        home / ".config",
    ):
        if candidate.is_dir():
            return candidate
    return home


def _base_url_from_pipeline_ui_settings() -> str | None:
    """Read host/port/path from DAISY Pipeline desktop settings if present."""
    settings = _local_appdata() / "pipeline-ui" / "settings.json"
    if not settings.is_file():
        return None
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    props = data.get("pipelineInstanceProps")
    if not isinstance(props, dict):
        return None
    ws = props.get("webservice")
    if not isinstance(ws, dict):
        return None
    host = str(ws.get("host") or "127.0.0.1").strip()
    port = ws.get("port")
    path = str(ws.get("path") or "/ws").strip() or "/ws"
    if not path.startswith("/"):
        path = "/" + path
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None
    if not host or port_i <= 0:
        return None
    return f"http://{host}:{port_i}{path}"


def candidate_base_urls() -> list[str]:
    urls: list[str] = []
    from_ui = _base_url_from_pipeline_ui_settings()
    if from_ui:
        urls.append(from_ui.rstrip("/"))
    for url in DEFAULT_BASE_URLS:
        u = url.rstrip("/")
        if u not in urls:
            urls.append(u)
    return urls


def _parse_alive(base_url: str, xml_text: str) -> PipelineStatus | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    tag = root.tag if isinstance(root.tag, str) else ""
    if tag.rsplit("}", 1)[-1] != "alive":
        return None
    auth = (root.attrib.get("authentication") or "").lower() == "true"
    localfs = (root.attrib.get("localfs") or "").lower() == "true"
    version = root.attrib.get("version") or ""
    return PipelineStatus(
        base_url=base_url.rstrip("/"),
        version=version,
        authentication=auth,
        localfs=localfs,
    )


def probe_pipeline(
    *,
    base_url: str | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> PipelineStatus | None:
    """Return status when a usable local Pipeline webservice is reachable.

    Secret-feature rules: must be alive, localfs enabled, and authentication
    disabled (Pipeline desktop local mode).
    """
    global _PIPELINE_STATUS_CACHE, _PIPELINE_STATUS_CACHED_AT
    urls = [base_url.rstrip("/")] if base_url else candidate_base_urls()
    for url in urls:
        try:
            resp = requests.get(f"{url}/alive", timeout=timeout)
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        status = _parse_alive(url, resp.text)
        if status is None:
            continue
        if status.authentication or not status.localfs:
            continue
        _PIPELINE_STATUS_CACHE = status
        _PIPELINE_STATUS_CACHED_AT = time.monotonic()
        return status
    return None


def pipeline_usable() -> bool:
    return probe_pipeline() is not None


def clear_pipeline_status_cache() -> None:
    """Reset the status-bar Pipeline probe cache."""
    global _PIPELINE_STATUS_CACHE, _PIPELINE_STATUS_CACHED_AT
    _PIPELINE_STATUS_CACHE = False
    _PIPELINE_STATUS_CACHED_AT = 0.0


def probe_pipeline_for_status() -> PipelineStatus | None:
    """Cached, short-timeout probe for the status bar (Ace-style optional tool)."""
    global _PIPELINE_STATUS_CACHE, _PIPELINE_STATUS_CACHED_AT
    now = time.monotonic()
    if _PIPELINE_STATUS_CACHE is not False:
        if now - _PIPELINE_STATUS_CACHED_AT < STATUS_PROBE_CACHE_TTL:
            return (
                _PIPELINE_STATUS_CACHE
                if isinstance(_PIPELINE_STATUS_CACHE, PipelineStatus)
                else None
            )
    status = probe_pipeline(timeout=STATUS_PROBE_TIMEOUT)
    _PIPELINE_STATUS_CACHE = status
    _PIPELINE_STATUS_CACHED_AT = now
    return status


def _file_uri(path: Path) -> str:
    return path.expanduser().resolve().as_uri()


def _job_request_xml(*, base_url: str, ncc: Path) -> str:
    script_href = f"{base_url.rstrip('/')}/scripts/{SCRIPT_ID}"
    source = _file_uri(ncc)
    # Match Pipeline desktop jobRequest shape (see pipeline-ui app logs).
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        f'<jobRequest xmlns="{PIPELINE_NS}">\n'
        "  <nicename>DAISY 2.02 Validator</nicename>\n"
        "  <priority>medium</priority>\n"
        f'  <script href="{script_href}"/>\n'
        f'  <input name="source"><item value="{source}"/></input>\n'
        '  <option name="timeToleranceMs">500</option>\n'
        "</jobRequest>"
    )


_JOB_ID_RE = re.compile(r'\bid="([0-9a-fA-F-]{36})"')


def _job_id_from_create(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        match = _JOB_ID_RE.search(xml_text)
        return match.group(1) if match else None
    jid = root.attrib.get("id")
    if jid:
        return jid
    match = _JOB_ID_RE.search(xml_text)
    return match.group(1) if match else None


def _job_status(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    return root.attrib.get("status")


def job_messages_text(xml_text: str) -> str:
    lines: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    for msg in root.findall(".//d:message", _NS):
        level = msg.attrib.get("level") or "INFO"
        content = msg.attrib.get("content") or "".join(msg.itertext()).strip()
        if content:
            lines.append(f"[{level}] {content}")
    # Fallback without namespace awareness
    if not lines:
        for elem in root.iter():
            tag = elem.tag if isinstance(elem.tag, str) else ""
            if tag.rsplit("}", 1)[-1] != "message":
                continue
            level = elem.attrib.get("level") or "INFO"
            content = elem.attrib.get("content") or "".join(elem.itertext()).strip()
            if content:
                lines.append(f"[{level}] {content}")
    return "\n".join(lines)


def create_daisy202_job(status: PipelineStatus, ncc: Path) -> str:
    url = f"{status.base_url}/jobs"
    body = _job_request_xml(base_url=status.base_url, ncc=ncc)
    resp = requests.post(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Pipeline job create failed HTTP {resp.status_code}: {resp.text[:500]}"
        )
    job_id = _job_id_from_create(resp.text)
    if not job_id:
        raise RuntimeError("Pipeline job create response missing job id")
    return job_id


def wait_for_job(
    status: PipelineStatus,
    job_id: str,
    *,
    timeout: float = JOB_TIMEOUT,
) -> tuple[str, str]:
    """Poll until SUCCESS/FAIL/ERROR. Returns (status, job_xml)."""
    url = f"{status.base_url}/jobs/{job_id}"
    deadline = time.monotonic() + timeout
    last_xml = ""
    while time.monotonic() < deadline:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Pipeline job status failed HTTP {resp.status_code}"
            )
        last_xml = resp.text
        st = _job_status(last_xml)
        if st in ("SUCCESS", "FAIL", "ERROR"):
            return st, last_xml
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Pipeline job timed out after {int(timeout)}s")


def download_html_report(
    status: PipelineStatus,
    job_id: str,
    dest_dir: Path,
) -> Path | None:
    """Download job result zip and return path to html-report.html if found."""
    url = f"{status.base_url}/jobs/{job_id}/result"
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "result.zip"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        # Also try the named port path used by Pipeline UI.
        alt = f"{status.base_url}/jobs/{job_id}/result/port/html-report"
        resp = requests.get(alt, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
    zip_path.write_bytes(resp.content)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
    except zipfile.BadZipFile:
        # Sometimes the endpoint returns the HTML directly.
        html_path = dest_dir / "html-report.html"
        if b"<html" in resp.content[:500].lower() or b"Validation Results" in resp.content:
            html_path.write_bytes(resp.content)
            return html_path
        return None

    matches = sorted(dest_dir.rglob("html-report.html"))
    if matches:
        return matches[0]
    # Any xhtml/html under the extract
    for pattern in ("*.html", "*.xhtml"):
        found = sorted(dest_dir.rglob(pattern))
        if found:
            return found[0]
    return None


def fetch_job_log(status: PipelineStatus, job_id: str) -> str:
    url = f"{status.base_url}/jobs/{job_id}/log"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return ""
    if resp.status_code != 200:
        return ""
    return resp.text


def delete_job(status: PipelineStatus, job_id: str) -> None:
    url = f"{status.base_url}/jobs/{job_id}"
    try:
        requests.delete(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass


def path_to_ncc(folder: Path) -> Path | None:
    from .publication import find_ncc

    return find_ncc(folder)