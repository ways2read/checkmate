"""EPUBCheck message id → authoritative docs / DAISY KB articles."""

from __future__ import annotations

from .ace_kb_map import KB_PUBLISHING_BASE, normalize_kb_url

# Official per-message catalog (includes Explanation text for many codes).
EPUBCHECK_MESSAGES_TITLE = "EPUBCheck message reference"
EPUBCHECK_MESSAGES_URL = "https://www.w3.org/publishing/epubcheck/docs/messages/"

# Accessibility-oriented EPUBCheck codes → specific DAISY KB articles.
# Keys are normalized as PREFIX_NNN (underscores), matching EPUBCheck docs.
# Paths are relative to KB_PUBLISHING_BASE.
EPUBCHECK_ACC_KB_PATHS: dict[str, tuple[str, str]] = {
    "ACC_001": ("DAISY KB: Images", "docs/html/images.html"),
    "ACC_002": ("DAISY KB: Forms", "docs/html/forms.html"),
    "ACC_008": ("DAISY KB: Landmarks", "docs/html/landmarks.html"),
    "ACC_009": ("DAISY KB: MathML", "docs/html/mathml.html"),
    "ACC_011": ("DAISY KB: Images", "docs/html/images.html"),
    "ACC_013": ("DAISY KB: Visual Separation", "docs/html/separation.html"),
    "ACC_014": ("DAISY KB: Text Resizing", "docs/css/text-resize.html"),
    "ACC_015": ("DAISY KB: Text Resizing", "docs/css/text-resize.html"),
    "ACC_016": ("DAISY KB: Text Resizing", "docs/css/text-resize.html"),
    "ACC_017": ("DAISY KB: Text Resizing", "docs/css/text-resize.html"),
}

# Selected high-traffic HTML / navigation codes that map cleanly to KB topics.
EPUBCHECK_OTHER_KB_PATHS: dict[str, tuple[str, str]] = {
    "HTM_017": ("DAISY KB: Language", "docs/html/lang.html"),
    "HTM_018": ("DAISY KB: Language", "docs/html/lang.html"),
    "HTM_019": ("DAISY KB: Language", "docs/html/lang.html"),
    "HTM_020": ("DAISY KB: Language", "docs/html/lang.html"),
    "HTM_021": ("DAISY KB: Language", "docs/html/lang.html"),
    "HTM_027": ("DAISY KB: Lists", "docs/html/lists.html"),
    "HTM_028": ("DAISY KB: Forms", "docs/html/forms.html"),
    "HTM_029": ("DAISY KB: Forms", "docs/html/forms.html"),
    "HTM_033": ("DAISY KB: Page Title", "docs/html/title.html"),
    "HTM_050": ("DAISY KB: Page List", "docs/navigation/pagelist.html"),
    "NAV_002": ("DAISY KB: Page List", "docs/navigation/pagelist.html"),
    "NAV_003": ("DAISY KB: Page List", "docs/navigation/pagelist.html"),
}


def normalize_epubcheck_code(code: str) -> str:
    """Normalize ``OPF-049`` / ``opf_049`` → ``OPF_049``."""
    raw = (code or "").strip().upper().replace("-", "_")
    return raw


def epubcheck_messages_resource() -> tuple[str, str]:
    return EPUBCHECK_MESSAGES_TITLE, EPUBCHECK_MESSAGES_URL


def kb_resource_for_epubcheck_code(code: str) -> tuple[str, str] | None:
    """Return (title, absolute https URL) for a mapped EPUBCheck code, or None."""
    key = normalize_epubcheck_code(code)
    if not key:
        return None
    entry = EPUBCHECK_ACC_KB_PATHS.get(key) or EPUBCHECK_OTHER_KB_PATHS.get(key)
    if not entry:
        return None
    title, rel = entry
    if not rel:
        return None
    return title, normalize_kb_url(KB_PUBLISHING_BASE + rel.lstrip("/"))


def looks_like_epubcheck_code(code: str) -> bool:
    """True when the code looks like an EPUBCheck message id (ACC_001, OPF-049, …)."""
    key = normalize_epubcheck_code(code)
    if not key or "_" not in key:
        return False
    prefix, _, rest = key.partition("_")
    if prefix not in {
        "ACC",
        "CHK",
        "CSS",
        "HTM",
        "MED",
        "NAV",
        "NCX",
        "OPF",
        "PKG",
        "RSC",
        "SCP",
    }:
        return False
    # Require a numeric (or numeric+letter) suffix, e.g. 049, 014A.
    return bool(rest) and rest[0].isdigit()
