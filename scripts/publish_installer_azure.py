"""Upload the Windows installer to Azure Blob Storage as CheckMate-setup.exe.

Destination (same parent as Fido betas ``Fido/beta/``):

    Fido/checkmate/CheckMate-setup.exe
    https://dl.daisy.org/tools/Fido/checkmate/CheckMate-setup.exe

Credentials match Fido unlock/beta publish (Azure AD preferred, SAS / AzCopy fallback):

    CHECKMATE_UNLOCK_PUBLISH_ACCOUNT_URL + CHECKMATE_UNLOCK_PUBLISH_CONTAINER
    FIDO_UNLOCK_PUBLISH_ACCOUNT_URL + FIDO_UNLOCK_PUBLISH_CONTAINER
    unlock_publish in checkmate.secrets.json (or sibling FIDO/fido.secrets.json)
    CHECKMATE_UNLOCK_PUBLISH_CONTAINER_SAS_URL / FIDO_UNLOCK_PUBLISH_CONTAINER_SAS_URL
    CHECKMATE_AZURE_BLOB_SAS / FIDO_AZURE_BLOB_SAS (AzCopy query string)

Skip from the installer script with CHECKMATE_SKIP_AZURE_PUBLISH=1 or -SkipAzurePublish.
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
from urllib.parse import quote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
BLOB_NAME = "Fido/checkmate/CheckMate-setup.exe"
PUBLIC_URL = "https://dl.daisy.org/tools/Fido/checkmate/CheckMate-setup.exe"
AZCOPY_BASE = "https://daisy.blob.core.windows.net/tools"
INSTALLER_CONTENT_TYPE = "application/vnd.microsoft.portable-executable"

PublishMode = Literal["aad", "sas"]


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


def _upload_aad(target: PublishTarget, local_path: Path) -> tuple[bool, str]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient, ContentSettings
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
            container=target.container_name, blob=BLOB_NAME
        )
        with local_path.open("rb") as stream:
            blob_client.upload_blob(
                stream,
                overwrite=True,
                content_settings=ContentSettings(content_type=INSTALLER_CONTENT_TYPE),
            )
    except Exception as e:
        return False, str(e)
    return True, ""


def _upload_sas(target: PublishTarget, local_path: Path) -> tuple[bool, str]:
    try:
        from azure.storage.blob import BlobClient, ContentSettings
    except ImportError as e:
        return False, f"azure packages missing: {e}"
    assert target.container_sas_url
    try:
        url = _build_blob_put_url(target.container_sas_url, BLOB_NAME)
        client = BlobClient.from_blob_url(url)
        with local_path.open("rb") as stream:
            client.upload_blob(
                stream,
                overwrite=True,
                content_settings=ContentSettings(content_type=INSTALLER_CONTENT_TYPE),
            )
    except Exception as e:
        return False, str(e)
    return True, ""


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish CheckMate-setup.exe to Azure Blob Storage"
    )
    parser.add_argument("--setup-exe", type=Path, required=True)
    args = parser.parse_args()
    setup = args.setup_exe.expanduser().resolve()
    if not setup.is_file():
        print(f"ERROR: installer not found: {setup}", file=sys.stderr)
        return 1

    sas_query = _blob_sas_query()
    if sas_query:
        dest = f"{AZCOPY_BASE}/{BLOB_NAME}{sas_query}"
        ok, err = _upload_azcopy(setup, dest)
        if not ok:
            print(f"ERROR: azcopy {BLOB_NAME}: {err}", file=sys.stderr)
            return 1
        print(f"Uploaded: {PUBLIC_URL}")
        return 0

    target, cfg_err = resolve_publish_target()
    if target is None:
        print(
            "ERROR: Azure publish is not configured.\n"
            "  Preferred: unlock_publish.account_url + container in checkmate.secrets.json "
            "(or sibling FIDO/fido.secrets.json), then az login\n"
            "  Or env: CHECKMATE_UNLOCK_PUBLISH_ACCOUNT_URL + CHECKMATE_UNLOCK_PUBLISH_CONTAINER "
            "(FIDO_UNLOCK_PUBLISH_* also accepted)\n"
            "  Or SAS: CHECKMATE_UNLOCK_PUBLISH_CONTAINER_SAS_URL / FIDO_AZURE_BLOB_SAS (AzCopy)\n"
            "  To skip: CHECKMATE_SKIP_AZURE_PUBLISH=1 or -SkipAzurePublish",
            file=sys.stderr,
        )
        if cfg_err:
            print(f"  (resolve error: {cfg_err})", file=sys.stderr)
        return 1

    if target.mode == "aad":
        ok, err = _upload_aad(target, setup)
    else:
        ok, err = _upload_sas(target, setup)
    if not ok:
        print(f"ERROR: upload {BLOB_NAME}: {err}", file=sys.stderr)
        return 1
    print(f"Uploaded: {BLOB_NAME}")
    print(f"Public URL: {PUBLIC_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
