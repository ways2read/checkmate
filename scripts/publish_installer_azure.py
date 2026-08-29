"""Upload the Windows/macOS installer and version.json to Azure Blob Storage.

Destination (same parent as Fido betas ``Fido/beta/``):

    Fido/checkmate/CheckMate-setup.exe
    Fido/checkmate/CheckMate-setup.dmg
    Fido/checkmate/version.json
    https://dl.daisy.org/tools/Fido/checkmate/version.json

``version.json`` keeps Windows and macOS versions as separate fields so each
platform's publish can update only its own keys. Fido reads this file from
Settings to report the latest CheckMate build.

Credentials match Fido unlock/beta publish (Azure AD preferred, SAS / AzCopy fallback):

    CHECKMATE_UNLOCK_PUBLISH_ACCOUNT_URL + CHECKMATE_UNLOCK_PUBLISH_CONTAINER
    FIDO_UNLOCK_PUBLISH_ACCOUNT_URL + FIDO_UNLOCK_PUBLISH_CONTAINER
    unlock_publish in checkmate.secrets.json (or sibling FIDO/fido.secrets.json)
    CHECKMATE_UNLOCK_PUBLISH_CONTAINER_SAS_URL / FIDO_UNLOCK_PUBLISH_CONTAINER_SAS_URL
    CHECKMATE_AZURE_BLOB_SAS / FIDO_AZURE_BLOB_SAS (AzCopy query string)

Skip from the installer scripts with CHECKMATE_SKIP_AZURE_PUBLISH=1,
``-SkipAzurePublish`` (Windows), or ``--skip-azure-publish`` (macOS).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
BLOB_PREFIX = "Fido/checkmate"
BLOB_SETUP_WINDOWS = f"{BLOB_PREFIX}/CheckMate-setup.exe"
BLOB_SETUP_MACOS = f"{BLOB_PREFIX}/CheckMate-setup.dmg"
BLOB_VERSION_JSON = f"{BLOB_PREFIX}/version.json"
PUBLIC_BASE = "https://dl.daisy.org/tools/Fido/checkmate"
PUBLIC_SETUP_WINDOWS_URL = f"{PUBLIC_BASE}/CheckMate-setup.exe"
PUBLIC_SETUP_MACOS_URL = f"{PUBLIC_BASE}/CheckMate-setup.dmg"
PUBLIC_VERSION_JSON_URL = f"{PUBLIC_BASE}/version.json"
AZCOPY_BASE = "https://daisy.blob.core.windows.net/tools"
INSTALLER_CONTENT_TYPE = "application/vnd.microsoft.portable-executable"
DMG_CONTENT_TYPE = "application/x-apple-diskimage"
VERSION_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
VERSION_JSON_CACHE_CONTROL = "public, max-age=60"

PublishMode = Literal["aad", "sas"]
PlatformName = Literal["windows", "macos"]

WINDOWS_VERSION_KEY = "windows_latest_version"
MACOS_VERSION_KEY = "macos_latest_version"
WINDOWS_URL_KEY = "windows_download_url"
MACOS_URL_KEY = "macos_download_url"


@dataclass(frozen=True)
class PublishTarget:
    mode: PublishMode
    account_url: str | None = None
    container_name: str | None = None
    container_sas_url: str | None = None


def _env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _load_unlock_publish_block() -> dict[str, Any]:
    paths = [
        REPO_ROOT / "checkmate.secrets.json",
        REPO_ROOT.parent / "FIDO" / "fido.secrets.json",
    ]
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        block = raw.get("unlock_publish")
        if isinstance(block, dict):
            return block
    return {}


def _validate_account_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid_account"
    if parsed.scheme != "https" or not parsed.netloc or ".blob." not in parsed.netloc.lower():
        return "invalid_account"
    if (parsed.path or "").strip("/"):
        return "invalid_account"
    return None


def resolve_publish_target() -> tuple[PublishTarget | None, str | None]:
    acct = _env(
        "CHECKMATE_UNLOCK_PUBLISH_ACCOUNT_URL",
        "FIDO_UNLOCK_PUBLISH_ACCOUNT_URL",
    ).rstrip("/")
    cont = _env(
        "CHECKMATE_UNLOCK_PUBLISH_CONTAINER",
        "CHECKMATE_UNLOCK_PUBLISH_CONTAINER_NAME",
        "FIDO_UNLOCK_PUBLISH_CONTAINER",
        "FIDO_UNLOCK_PUBLISH_CONTAINER_NAME",
    )
    block = _load_unlock_publish_block()
    if not acct:
        acct = str(block.get("account_url") or "").strip().rstrip("/")
    if not cont:
        cont = str(block.get("container") or block.get("container_name") or "").strip()
    if acct and cont:
        err = _validate_account_url(acct)
        if err:
            return None, err
        return PublishTarget("aad", account_url=acct, container_name=cont), None

    sas = _env(
        "CHECKMATE_UNLOCK_PUBLISH_CONTAINER_SAS_URL",
        "FIDO_UNLOCK_PUBLISH_CONTAINER_SAS_URL",
    )
    if not sas:
        sas = str(block.get("container_sas_url") or block.get("container_sas") or "").strip()
    if sas:
        parsed = urlparse(sas)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.path in {"", "/"}:
            return None, "invalid_url"
        return PublishTarget("sas", container_sas_url=sas), None
    return None, "not_configured"


def _blob_sas_query() -> str:
    query = _env("CHECKMATE_AZURE_BLOB_SAS", "FIDO_AZURE_BLOB_SAS")
    if query and not query.startswith("?"):
        return "?" + query
    return query


def _build_blob_put_url(container_sas_url: str, blob_name: str) -> str:
    parsed = urlparse(container_sas_url)
    container_path = parsed.path.rstrip("/")
    if not container_path:
        raise ValueError("container path missing")
    segments = [s for s in blob_name.split("/") if s]
    encoded = "/".join(quote(seg, safe="-_.!~*'()") for seg in segments)
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}{container_path}/{encoded}{query}"


def project_version() -> str:
    init = REPO_ROOT / "checkmate" / "__init__.py"
    if init.is_file():
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("\"'")
    return "0.0.0"


def empty_version_manifest() -> dict[str, str]:
    return {
        WINDOWS_VERSION_KEY: "",
        MACOS_VERSION_KEY: "",
        WINDOWS_URL_KEY: PUBLIC_SETUP_WINDOWS_URL,
        MACOS_URL_KEY: PUBLIC_SETUP_MACOS_URL,
    }


def merge_version_manifest(
    existing: dict[str, Any] | None,
    *,
    platform: PlatformName,
    version: str,
) -> dict[str, str]:
    """Update one platform's version; keep the other platform's published values."""
    data = empty_version_manifest()
    if isinstance(existing, dict):
        for key in data:
            val = existing.get(key)
            if isinstance(val, str) and val.strip():
                data[key] = val.strip()
    version = version.strip()
    if platform == "windows":
        data[WINDOWS_VERSION_KEY] = version
        data[WINDOWS_URL_KEY] = PUBLIC_SETUP_WINDOWS_URL
    else:
        data[MACOS_VERSION_KEY] = version
        data[MACOS_URL_KEY] = PUBLIC_SETUP_MACOS_URL
    return data


def write_version_json(path: Path, manifest: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_version_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def fetch_public_version_manifest() -> dict[str, Any] | None:
    req = Request(PUBLIC_VERSION_JSON_URL, headers={"User-Agent": "CheckMate-publish/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            raw = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError, TypeError, ValueError, TimeoutError):
        return None
    return raw if isinstance(raw, dict) else None


def _content_settings(content_type: str):
    from azure.storage.blob import ContentSettings

    kwargs: dict[str, str] = {"content_type": content_type}
    if content_type.startswith("application/json"):
        kwargs["cache_control"] = VERSION_JSON_CACHE_CONTROL
    return ContentSettings(**kwargs)


def _upload_aad(
    target: PublishTarget, local_path: Path, blob_name: str, content_type: str
) -> tuple[bool, str]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:
        return False, f"azure packages missing: {e}"
    assert target.account_url and target.container_name
    try:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        service = BlobServiceClient(
            account_url=target.account_url.rstrip("/") + "/",
            credential=credential,
        )
        blob_client = service.get_blob_client(
            container=target.container_name, blob=blob_name
        )
        with local_path.open("rb") as stream:
            blob_client.upload_blob(
                stream,
                overwrite=True,
                content_settings=_content_settings(content_type),
            )
    except Exception as e:
        return False, str(e)
    return True, ""


def _download_aad(target: PublishTarget, blob_name: str) -> dict[str, Any] | None:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return None
    assert target.account_url and target.container_name
    try:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        service = BlobServiceClient(
            account_url=target.account_url.rstrip("/") + "/",
            credential=credential,
        )
        blob_client = service.get_blob_client(
            container=target.container_name, blob=blob_name
        )
        raw = json.loads(blob_client.download_blob().readall().decode("utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _upload_sas(
    target: PublishTarget, local_path: Path, blob_name: str, content_type: str
) -> tuple[bool, str]:
    try:
        from azure.storage.blob import BlobClient
    except ImportError as e:
        return False, f"azure packages missing: {e}"
    assert target.container_sas_url
    try:
        url = _build_blob_put_url(target.container_sas_url, blob_name)
        client = BlobClient.from_blob_url(url)
        with local_path.open("rb") as stream:
            client.upload_blob(
                stream,
                overwrite=True,
                content_settings=_content_settings(content_type),
            )
    except Exception as e:
        return False, str(e)
    return True, ""


def _download_sas(target: PublishTarget, blob_name: str) -> dict[str, Any] | None:
    try:
        from azure.storage.blob import BlobClient
    except ImportError:
        return None
    assert target.container_sas_url
    try:
        url = _build_blob_put_url(target.container_sas_url, blob_name)
        client = BlobClient.from_blob_url(url)
        raw = json.loads(client.download_blob().readall().decode("utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _upload_azcopy(local_path: Path, dest_url_with_sas: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["azcopy", "copy", str(local_path), dest_url_with_sas, "--overwrite=true"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, "azcopy not found on PATH"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"azcopy exit {proc.returncode}"
    return True, ""


def _upload_file(
    target: PublishTarget | None,
    *,
    sas_query: str,
    local_path: Path,
    blob_name: str,
    content_type: str,
) -> tuple[bool, str]:
    if sas_query:
        dest = f"{AZCOPY_BASE}/{blob_name}{sas_query}"
        return _upload_azcopy(local_path, dest)
    assert target is not None
    if target.mode == "aad":
        return _upload_aad(target, local_path, blob_name, content_type)
    return _upload_sas(target, local_path, blob_name, content_type)


def load_existing_manifest(
    *,
    local_path: Path | None,
    target: PublishTarget | None,
) -> dict[str, Any] | None:
    """Prefer the live blob so a Windows publish does not wipe a macOS version."""
    if target is not None:
        if target.mode == "aad":
            remote = _download_aad(target, BLOB_VERSION_JSON)
        else:
            remote = _download_sas(target, BLOB_VERSION_JSON)
        if remote:
            return remote
    public = fetch_public_version_manifest()
    if public:
        return public
    if local_path is not None:
        return load_version_json(local_path)
    return None


def setup_blob_for_platform(platform: PlatformName) -> tuple[str, str, str]:
    if platform == "macos":
        return BLOB_SETUP_MACOS, DMG_CONTENT_TYPE, PUBLIC_SETUP_MACOS_URL
    return BLOB_SETUP_WINDOWS, INSTALLER_CONTENT_TYPE, PUBLIC_SETUP_WINDOWS_URL


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish CheckMate installer and version.json to Azure Blob Storage"
    )
    parser.add_argument(
        "--setup-exe",
        "--setup-dmg",
        dest="setup_exe",
        type=Path,
        default=None,
        help="Path to CheckMate-setup.exe (Windows) or the signed .dmg (macOS)",
    )
    parser.add_argument(
        "--platform",
        choices=("windows", "macos"),
        default="windows",
        help="Which platform's version field to update in version.json",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Version string for this platform (default: checkmate/__init__.py)",
    )
    parser.add_argument(
        "--write-version-json",
        type=Path,
        default=None,
        help="Write the merged version.json here (default: next to --setup-exe)",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="Do not upload the installer; still write and upload version.json",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Write version.json locally only (no Azure)",
    )
    args = parser.parse_args()
    platform: PlatformName = args.platform
    version = (args.version or "").strip() or project_version()

    setup = args.setup_exe.expanduser().resolve() if args.setup_exe else None
    if setup is not None and not setup.is_file() and not args.skip_setup:
        print(f"ERROR: installer not found: {setup}", file=sys.stderr)
        return 1
    if setup is None and not args.skip_setup and not args.skip_upload:
        print(
            "ERROR: --setup-exe / --setup-dmg is required unless --skip-setup or --skip-upload",
            file=sys.stderr,
        )
        return 1

    json_path = args.write_version_json
    if json_path is None:
        if setup is not None:
            json_path = setup.parent / "version.json"
        else:
            json_path = REPO_ROOT / "installer" / "Output" / "version.json"
    json_path = json_path.expanduser().resolve()

    sas_query = "" if args.skip_upload else _blob_sas_query()
    target: PublishTarget | None = None
    if not args.skip_upload and not sas_query:
        target, cfg_err = resolve_publish_target()
        if target is None:
            print(
                "ERROR: Azure publish is not configured.\n"
                "  Preferred: unlock_publish.account_url + container in checkmate.secrets.json "
                "(or sibling FIDO/fido.secrets.json), then az login\n"
                "  Or env: CHECKMATE_UNLOCK_PUBLISH_ACCOUNT_URL + CHECKMATE_UNLOCK_PUBLISH_CONTAINER "
                "(FIDO_UNLOCK_PUBLISH_* also accepted)\n"
                "  Or SAS: CHECKMATE_UNLOCK_PUBLISH_CONTAINER_SAS_URL / FIDO_AZURE_BLOB_SAS (AzCopy)\n"
                "  To skip: CHECKMATE_SKIP_AZURE_PUBLISH=1, -SkipAzurePublish, or --skip-azure-publish",
                file=sys.stderr,
            )
            if cfg_err:
                print(f"  (resolve error: {cfg_err})", file=sys.stderr)
            return 1

    existing = load_existing_manifest(local_path=json_path, target=target)
    manifest = merge_version_manifest(existing, platform=platform, version=version)
    write_version_json(json_path, manifest)
    print(f"Wrote {json_path}")
    print(f"  {WINDOWS_VERSION_KEY}: {manifest[WINDOWS_VERSION_KEY] or '(none)'}")
    print(f"  {MACOS_VERSION_KEY}: {manifest[MACOS_VERSION_KEY] or '(none)'}")

    if args.skip_upload:
        return 0

    ok, err = _upload_file(
        target,
        sas_query=sas_query,
        local_path=json_path,
        blob_name=BLOB_VERSION_JSON,
        content_type=VERSION_JSON_CONTENT_TYPE,
    )
    if not ok:
        print(f"ERROR: upload {BLOB_VERSION_JSON}: {err}", file=sys.stderr)
        return 1
    print(f"Uploaded: {PUBLIC_VERSION_JSON_URL}")

    if args.skip_setup:
        return 0
    assert setup is not None
    blob_name, content_type, public_url = setup_blob_for_platform(platform)
    ok, err = _upload_file(
        target,
        sas_query=sas_query,
        local_path=setup,
        blob_name=blob_name,
        content_type=content_type,
    )
    if not ok:
        print(f"ERROR: upload {blob_name}: {err}", file=sys.stderr)
        return 1
    print(f"Uploaded: {public_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
