"""Shared document-image backend API (EPUB/PDF and Fido Image Utility)."""
from abc import ABC, abstractmethod
import logging
import os
from typing import Any, Callable, List, Optional

logger = logging.getLogger("fido")

# Sentinel for "use active Word document" (Windows only)
ACTIVE_WORD_DOCUMENT = "active_word_document"


# Capability keys: when False, the dialog disables/hides the corresponding UI.
CAP_EDIT_IMAGE = "supports_edit_image"
CAP_DELETE_IMAGE = "supports_delete_image"
CAP_COMPRESS_IMAGE = "supports_compress_image"
CAP_CONVERT_TO_TEXT = "supports_convert_to_text"
CAP_SPELL_CHECK = "supports_spell_check"
CAP_BATCH_SPELL_CHECK = "supports_batch_spell_check"
CAP_BATCH_COMPRESS = "supports_batch_compress"
CAP_BATCH_CONVERT = "supports_batch_convert"
CAP_DELETE_TINY = "supports_delete_tiny"
CAP_EXTENDED_DESCRIPTION = "supports_extended_description"

# Default capabilities: all out-of-scope features disabled for this plan.
DEFAULT_CAPABILITIES = {
    CAP_EDIT_IMAGE: False,
    CAP_DELETE_IMAGE: False,
    CAP_COMPRESS_IMAGE: False,
    CAP_CONVERT_TO_TEXT: False,
    CAP_SPELL_CHECK: False,
    CAP_BATCH_SPELL_CHECK: False,
    CAP_BATCH_COMPRESS: False,
    CAP_BATCH_CONVERT: False,
    CAP_DELETE_TINY: False,
    CAP_EXTENDED_DESCRIPTION: False,
}


def load_image_result(image_path: str, alt_text: str, is_decorative: bool) -> dict:
    """Build the dict returned by load_image(index)."""
    return {
        "image_path": image_path,
        "alt_text": alt_text,
        "is_decorative": is_decorative,
    }


def _context_params():
    """Return (context_size, max_blocks_to_scan, max_chars) from user settings when available."""
    try:
        from checkmate.doc_images._fido_config import user_settings
        raw = user_settings.get("text_context_size", 5)
    except Exception:
        raw = 5
    try:
        n = int(raw) if raw not in (None, "") else 5
    except (TypeError, ValueError):
        n = 5
    context_size = max(1, min(10, n))
    return context_size, 2 * context_size, 500 * context_size


def _cap_context_string(s: str, max_chars: int) -> str:
    """Truncate context string to max_chars (from _context_params)."""
    if not s or max_chars <= 0:
        return s
    return s[:max_chars] if len(s) > max_chars else s


class DocumentImageBackend(ABC):
    """
    Abstract backend for document image alt-text operations.

    The image utility dialog holds a backend instance and delegates all
    document/image operations to it. Export and Announce are handled by
    the dialog (iterate load_image / use current alt + TTS); no backend
    method required for those.
    """

    def __init__(self, dialog: Any = None, temp_dir: str | None = None):
        """
        :param dialog: Optional ImageUtilityDialog (progress / UI).
        :param temp_dir: Directory for extracts and temp image files.
            When None, uses Fido TEMP_DIR if available, else system temp.
        """
        self.dialog = dialog
        self._temp_dir_override = temp_dir
        self._capabilities = dict(DEFAULT_CAPABILITIES)

    def _resolve_temp_dir(self) -> str:
        if self._temp_dir_override:
            return self._temp_dir_override
        try:
            from checkmate.doc_images._fido_config import TEMP_DIR
            return TEMP_DIR
        except Exception:
            import tempfile
            return tempfile.gettempdir()

    @property
    def capabilities(self) -> dict:
        """Capability flags for UI enable/disable. Keys are CAP_* constants."""
        return self._capabilities

    def get_capability(self, key: str) -> bool:
        return self._capabilities.get(key, False)

    # --- Document lifecycle ---

    @abstractmethod
    def get_document_display_name(self) -> str:
        """Return a short name for the document (e.g. filename) for title/label."""
        pass

    @abstractmethod
    def open_document(self, source: Optional[str]) -> bool:
        """
        Open the document. source is None or ACTIVE_WORD_DOCUMENT for
        "active Word document" (Windows), or a file path string.
        Returns True on success.
        """
        pass

    @abstractmethod
    def save_document(self) -> bool:
        """Persist current state (e.g. alt text). Returns True on success."""
        pass

    def close(self) -> None:
        """Release resources (e.g. close file handles). No-op for Word."""
        pass

    # --- Image list and current image ---

    @abstractmethod
    def get_image_count(self) -> int:
        """Return the number of images in the document."""
        pass

    @abstractmethod
    def load_image(self, index: int) -> Optional[dict]:
        """
        Load the image at index. Returns a dict with:
          - image_path: path to a temp file for display (or empty if failed)
          - alt_text: current alt text for the image
          - is_decorative: whether the image is marked decorative
        Returns None if index out of range or load failed.
        """
        pass

    @abstractmethod
    def set_alt_text(self, index: int, text: str) -> bool:
        """Write alt text for the image at index. Returns True on success."""
        pass

    @abstractmethod
    def set_decorative(self, index: int, is_decorative: bool) -> bool:
        """
        Mark the image at index as decorative or not. Backend may clear
        alt text when is_decorative is True. Returns True on success.
        """
        pass

    def get_alt_text(self, index: int) -> str:
        """
        Return current alt text for the image at index (for undescribed
        navigation). Default uses load_image and returns alt_text; backends
        may override for efficiency.
        """
        result = self.load_image(index)
        if result is None:
            return ""
        return result.get("alt_text", "").strip()

    def sync_embedded_image_from_path(self, index: int, path: str) -> bool:
        """
        Copy image bytes from ``path`` into the document's embedded image at
        ``index`` (e.g. after writing XMP into a temp extract). No-op if unsupported.
        """
        if hasattr(self, "replace_image_from_file"):
            return self.replace_image_from_file(index, path)
        return False

    def get_context(self, index: int) -> str:
        """
        Return surrounding text for the image at index (for {context} in
        prompts). Return "" if not supported.
        """
        return ""

    def get_shape(self, index: int) -> Any:
        """
        Return the native shape/picture object at index, if any (e.g. for
        Word: Select/highlight in document). Return None if not supported.
        """
        return None

    # --- Bulk describe ---

    def describe_all_images(
        self,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None,
        image_dialog: Any = None,
    ) -> int:
        """
        Run bulk describe for all (or undescribed) images. progress_callback
        receives (current_index, total, message) and returns True to continue.
        image_dialog is the ImageUtilityDialog for describer/UI. Returns
        number of images processed.
        """
        return 0
