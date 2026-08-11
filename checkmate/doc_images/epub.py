"""EPUB on-disc document image backend."""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from checkmate.doc_images.api import (
    CAP_EXTENDED_DESCRIPTION,
    DocumentImageBackend,
    _cap_context_string,
    _context_params,
    load_image_result,
)

logger = logging.getLogger("fido")


def _epub_extract_text_from_element(element) -> str:
    parts = []
    if element.text and element.text.strip():
        parts.append(element.text.strip())
    for child in element:
        parts.append(_epub_extract_text_from_element(child))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(parts)


def _strip_html_prefix_from_xhtml_file(content_path: str) -> None:
    """Remove html: tag prefix from XHTML so output uses normal <p>, <img> etc.
    Normalize root to xmlns + xmlns:epub only (no xmlns:html, no xmlns:ns1).
    """
    try:
        with open(content_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"</html:([a-zA-Z][a-zA-Z0-9:-]*)>", r"</\1>", content)
        content = re.sub(r"<html:([a-zA-Z][a-zA-Z0-9:-]*)(\s|>)", r"<\1\2", content)
        xhtml_ns = "http://www.w3.org/1999/xhtml"
        idpf_ns = "http://www.idpf.org/2007/ops"
        # Remove xmlns:html so we don't end up with both xmlns:html and xmlns= (invalid/redundant)
        content = re.sub(r"\s*xmlns:html\s*=\s*[\"']" + re.escape(xhtml_ns) + r"[\"']", "", content)
        # Normalize EPUB namespace: ns1 -> epub (ElementTree often emits xmlns:ns1)
        content = re.sub(r"\s*xmlns:ns1\s*=\s*[\"']" + re.escape(idpf_ns) + r"[\"']", "", content)
        content = content.replace("ns1:", "epub:")
        if "xmlns:epub=" not in content:
            content = re.sub(r"(<html)(\s|>)", r'\1 xmlns:epub="' + idpf_ns + r'"\2', content, count=1)
        # Ensure default XHTML namespace on root (unprefixed elements need it)
        if ('xmlns="' + xhtml_ns + '"' not in content) and ("xmlns='" + xhtml_ns + "'" not in content):
            content = re.sub(r"(<html)(\s|>)", r'\1 xmlns="' + xhtml_ns + r'"\2', content, count=1)
        with open(content_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.debug("Could not strip html: prefix from %s: %s", content_path, e)


class EpubOnDiscBackend(DocumentImageBackend):
    """Backend for .epub on disc. Extract to temp, img alt; repackage on save. No decorative."""

    def __init__(self, dialog=None, temp_dir: str | None = None):
        super().__init__(dialog, temp_dir=temp_dir)
        self._capabilities[CAP_EXTENDED_DESCRIPTION] = False  # EPUB: extended description not supported
        self._document_path = ""
        self._extracted_dir = ""
        self._opf_dir = ""
        self._images_data: List[dict] = []

    def get_document_display_name(self) -> str:
        return os.path.basename(self._document_path) if self._document_path else ""

    def open_document(self, source: Optional[str]) -> bool:
        if not source or not os.path.isfile(source):
            return False
        import zipfile
        from xml.etree import ElementTree as ET

        self._document_path = os.path.realpath(source)
        self._images_data = []
        base = os.path.splitext(os.path.basename(self._document_path))[0]
        self._extracted_dir = os.path.join(self._resolve_temp_dir(), f"epub_backend_{base}")
        if os.path.exists(self._extracted_dir):
            try:
                import shutil
                shutil.rmtree(self._extracted_dir)
            except Exception:
                pass
        try:
            with zipfile.ZipFile(self._document_path, "r") as zf:
                zf.extractall(self._extracted_dir)
        except Exception as e:
            logger.debug("EpubOnDiscBackend extract failed: %s", e)
            return False

        container_path = os.path.join(self._extracted_dir, "META-INF", "container.xml")
        if not os.path.isfile(container_path):
            return False
        try:
            tree = ET.parse(container_path)
            root = tree.getroot()
            rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            if rootfile is None:
                return False
            opf_path = rootfile.get("full-path")
            if not opf_path:
                return False
            full_opf = os.path.join(self._extracted_dir, opf_path)
            self._opf_dir = os.path.dirname(opf_path)
            opf_tree = ET.parse(full_opf)
            opf_root = opf_tree.getroot()
            manifest = {}
            for item in opf_root.findall(".//{http://www.idpf.org/2007/opf}item"):
                href = item.get("href")
                mt = item.get("media-type", "")
                if href and mt in ("application/xhtml+xml", "text/html"):
                    manifest[item.get("id")] = os.path.normpath(os.path.join(self._extracted_dir, self._opf_dir, href.replace("/", os.sep)))
            spine = []
            for ref in opf_root.findall(".//{http://www.idpf.org/2007/opf}itemref"):
                idref = ref.get("idref")
                if idref in manifest:
                    spine.append(manifest[idref])
        except Exception as e:
            logger.debug("EpubOnDiscBackend parse OPF failed: %s", e)
            return False

        for content_path in spine:
            if not os.path.isfile(content_path):
                continue
            try:
                tree = ET.parse(content_path)
                root = tree.getroot()
                content_dir = os.path.dirname(content_path)
                for elem in root.iter():
                    local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if local.lower() != "img":
                        continue
                    src = elem.get("src")
                    if not src:
                        continue
                    if src.startswith("/"):
                        image_path = os.path.normpath(os.path.join(self._extracted_dir, src.lstrip("/").replace("/", os.sep)))
                    else:
                        image_path = os.path.normpath(os.path.join(content_dir, src.replace("/", os.sep)))
                    if not os.path.isfile(image_path):
                        continue
                    alt = (elem.get("alt") or "").strip()
                    role = (elem.get("role") or "").strip()
                    is_decorative = (not alt) or (role == "presentation")
                    _, _, max_chars = _context_params()
                    context = _cap_context_string(_epub_extract_text_from_element(root), max_chars)
                    self._images_data.append({
                        "img_element": elem,
                        "image_path": image_path,
                        "context": context,
                        "content_path": content_path,
                        "tree": tree,
                        "alt_text": alt,
                        "is_decorative": is_decorative,
                    })
            except Exception as e:
                logger.debug("EpubOnDiscBackend content %s: %s", content_path, e)
        return True

    def save_document(self) -> bool:
        if not self._extracted_dir or not self._document_path:
            return True
        try:
            import zipfile
            with zipfile.ZipFile(self._document_path, "w", zipfile.ZIP_DEFLATED) as zf:
                mimetype = os.path.join(self._extracted_dir, "mimetype")
                if os.path.isfile(mimetype):
                    zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
                for root, _dirs, files in os.walk(self._extracted_dir):
                    for f in files:
                        if f == "mimetype":
                            continue
                        path = os.path.join(root, f)
                        arcname = os.path.relpath(path, self._extracted_dir)
                        zf.write(path, arcname)
            return True
        except Exception as e:
            logger.debug("EpubOnDiscBackend save failed: %s", e)
            return False

    def get_image_count(self) -> int:
        return len(self._images_data)

    def load_image(self, index: int) -> Optional[dict]:
        if index < 0 or index >= len(self._images_data):
            return None
        import shutil
        rec = self._images_data[index]
        ext = os.path.splitext(rec["image_path"])[1].lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".gif"):
            ext = ".png"
        dest_path = os.path.join(self._resolve_temp_dir(), f"epub_image_{index}{ext}")
        try:
            shutil.copy2(rec["image_path"], dest_path)
        except Exception as e:
            logger.debug("EpubOnDiscBackend copy: %s", e)
            return load_image_result("", rec["alt_text"], rec.get("is_decorative", False))
        return load_image_result(dest_path, rec["alt_text"], rec.get("is_decorative", False))

    def set_alt_text(self, index: int, text: str) -> bool:
        if index < 0 or index >= len(self._images_data):
            return False
        rec = self._images_data[index]
        elem = rec["img_element"]
        elem.set("alt", text)
        rec["alt_text"] = text
        rec["is_decorative"] = not (text or "").strip()
        if rec["is_decorative"]:
            elem.set("role", "presentation")
        else:
            elem.attrib.pop("role", None)
        try:
            rec["tree"].write(rec["content_path"], encoding="utf-8", xml_declaration=True, method="xml")
            _strip_html_prefix_from_xhtml_file(rec["content_path"])
        except Exception as e:
            logger.debug("EpubOnDiscBackend write: %s", e)
            return False
        return True

    def sync_embedded_image_from_path(self, index: int, path: str) -> bool:
        if index < 0 or index >= len(self._images_data) or not path or not os.path.isfile(path):
            return False
        import shutil

        dest = self._images_data[index].get("image_path")
        if not dest:
            return False
        try:
            shutil.copy2(path, dest)
            return True
        except Exception as e:
            logger.debug("EpubOnDiscBackend sync_embedded_image_from_path: %s", e)
            return False

    def set_decorative(self, index: int, is_decorative: bool) -> bool:
        if is_decorative:
            return self.set_alt_text(index, "")
        # Clearing decorative must keep existing alt (batch describe sets alt then set_decorative(False)).
        if index < 0 or index >= len(self._images_data):
            return False
        rec = self._images_data[index]
        elem = rec["img_element"]
        elem.attrib.pop("role", None)
        rec["is_decorative"] = False
        try:
            rec["tree"].write(rec["content_path"], encoding="utf-8", xml_declaration=True, method="xml")
            _strip_html_prefix_from_xhtml_file(rec["content_path"])
        except Exception as e:
            logger.debug("EpubOnDiscBackend write (clear decorative): %s", e)
            return False
        return True

    def get_context(self, index: int) -> str:
        if index < 0 or index >= len(self._images_data):
            return ""
        _, _, max_chars = _context_params()
        return _cap_context_string(self._images_data[index].get("context", "") or "", max_chars)


# ---------------------------------------------------------------------------
# PDF-on-disc backend (tagged PDF, PyMuPDF)
# ---------------------------------------------------------------------------
