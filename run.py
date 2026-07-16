#!/usr/bin/env python3
"""Launch eBraille Checker GUI."""

from __future__ import annotations

import os

import certifi

from app.main import run_app


def _configure_tls_ca_bundle() -> None:
    """Point HTTPS clients at certifi's CA bundle in frozen builds."""
    ca_path = certifi.where()
    # Respect existing overrides if the user/admin already set one.
    os.environ.setdefault("SSL_CERT_FILE", ca_path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)


if __name__ == "__main__":
    _configure_tls_ca_bundle()
    run_app()
