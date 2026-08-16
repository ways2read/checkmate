"""User-visible name for the image and alt-text AI review feature."""

from ..i18n import _

# Save-as stem for HTML/Markdown exports (not translated).
FEATURE_FILENAME_STEM = "ai_image_sniff_test"
# Previous save stem; still recognized beside older export folders.
_LEGACY_FILENAME_STEM = "ai_image_inspector"


def feature_title() -> str:
    """Progress, dialog, report, and message-box title."""
    return _("AI Image Sniff Test")


def feature_run_button_label() -> str:
    """Inventory-report button to start a sniff test."""
    return _("Run AI Image &Sniff Test…")


def feature_html_basenames() -> frozenset[str]:
    """Canonical and legacy HTML filenames written beside an export."""
    return frozenset(
        {
            f"{FEATURE_FILENAME_STEM}.html",
            f"{_LEGACY_FILENAME_STEM}.html",
        }
    )
