"""Document-image backends and headless alt-text export.

Shared by Fido Image Utility and (via copy or import) CheckMate.
"""

from checkmate.doc_images.api import (
    ACTIVE_WORD_DOCUMENT,
    CAP_BATCH_COMPRESS,
    CAP_BATCH_CONVERT,
    CAP_BATCH_SPELL_CHECK,
    CAP_COMPRESS_IMAGE,
    CAP_CONVERT_TO_TEXT,
    CAP_DELETE_IMAGE,
    CAP_DELETE_TINY,
    CAP_EDIT_IMAGE,
    CAP_EXTENDED_DESCRIPTION,
    CAP_SPELL_CHECK,
    DEFAULT_CAPABILITIES,
    DocumentImageBackend,
    load_image_result,
)
from checkmate.doc_images.epub import EpubOnDiscBackend
from checkmate.doc_images.export import (
    AltTextExportResult,
    export_alt_text_report,
    export_document_alt_text,
    open_document_backend,
)
from checkmate.doc_images.html import HtmlOnDiscBackend
from checkmate.doc_images.pdf import PdfOnDiscBackend

__all__ = [
    "ACTIVE_WORD_DOCUMENT",
    "AltTextExportResult",
    "CAP_BATCH_COMPRESS",
    "CAP_BATCH_CONVERT",
    "CAP_BATCH_SPELL_CHECK",
    "CAP_COMPRESS_IMAGE",
    "CAP_CONVERT_TO_TEXT",
    "CAP_DELETE_IMAGE",
    "CAP_DELETE_TINY",
    "CAP_EDIT_IMAGE",
    "CAP_EXTENDED_DESCRIPTION",
    "CAP_SPELL_CHECK",
    "DEFAULT_CAPABILITIES",
    "DocumentImageBackend",
    "EpubOnDiscBackend",
    "HtmlOnDiscBackend",
    "PdfOnDiscBackend",
    "export_alt_text_report",
    "export_document_alt_text",
    "load_image_result",
    "open_document_backend",
]
