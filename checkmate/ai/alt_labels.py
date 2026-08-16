"""User-visible name for the image and alt-text AI review feature."""

from ..i18n import _

# Save-as stem for HTML/Markdown exports (not translated).
FEATURE_FILENAME_STEM = "ai_image_inspector"


def feature_title() -> str:
    """Progress, dialog, report, and message-box title."""
    return _("AI Image Inspector")


def feature_run_button_label() -> str:
    """Inventory-report button to start an inspection."""
    return _("Run AI &Image Inspector…")
