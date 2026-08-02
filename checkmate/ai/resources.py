"""Curated learning resources by checker source."""

from __future__ import annotations

from ..models import Issue

# Injected into the system prompt; models should prefer these over inventing URLs.
RESOURCE_MAP: dict[str, list[tuple[str, str]]] = {
    "Ace": [
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/",
        ),
        (
            "Ace by DAISY",
            "https://daisy.github.io/ace/",
        ),
    ],
    "EPUBCheck": [
        (
            "EPUBCheck messages",
            "https://github.com/w3c/epubcheck/wiki/",
        ),
        (
            "EPUB 3 Accessibility Guidelines",
            "https://www.w3.org/publishing/a11y/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/",
        ),
    ],
    "eBraille Checker": [
        (
            "eBraille standard",
            "https://daisy.org/s/ebraille/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/",
        ),
    ],
    "veraPDF": [
        (
            "veraPDF",
            "https://verapdf.org/",
        ),
        (
            "PDF/UA",
            "https://www.pdfa.org/resource/iso-14289-pdfua/",
        ),
    ],
    "DAISY Pipeline": [
        (
            "DAISY Pipeline",
            "https://daisy.github.io/pipeline/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/",
        ),
    ],
}

_DEFAULT_RESOURCES: list[tuple[str, str]] = [
    (
        "DAISY Accessible Publishing Knowledge Base",
        "https://kb.daisy.org/",
    ),
]


def resources_for_issue(issue: Issue) -> list[tuple[str, str]]:
    source = (issue.source or "").strip()
    if source in RESOURCE_MAP:
        return list(RESOURCE_MAP[source])
    # EPUB merged runs may leave source empty; guess from code prefixes.
    code = (issue.code or "").lower()
    if code.startswith("epub") or "opf" in code or "rsc-" in code:
        return list(RESOURCE_MAP["EPUBCheck"])
    if "wcag" in code or "epub-image" in code:
        return list(RESOURCE_MAP["Ace"])
    return list(_DEFAULT_RESOURCES)


def resources_prompt_block(issue: Issue) -> str:
    lines = ["Trusted resources (prefer these links in Learn more):"]
    for title, url in resources_for_issue(issue):
        lines.append(f"- {title}: {url}")
    return "\n".join(lines)
