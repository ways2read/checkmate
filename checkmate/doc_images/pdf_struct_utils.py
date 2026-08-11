"""
Low-level tagged-PDF structure tree helpers (PyMuPDF / fitz).
Shared by the language utility and image utility PDF backend.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

# Structure types treated as language-taggable content blocks.
# /LI and /Lbl are omitted — list items are containers; labels are markers, not body text.
PDF_CONTENT_BLOCK_TYPES = frozenset({
    "/P",
    "/H1",
    "/H2",
    "/H3",
    "/H4",
    "/H5",
    "/H6",
    "/Caption",
})

# Walked for nesting context only (not emitted as editable blocks).
PDF_LIST_STRUCT_TYPES = frozenset({"/L", "/LI"})

# /LBody may hold MCID text directly when the exporter did not wrap it in /P.
PDF_LBODY_STRUCT_TYPE = "/LBody"

PDF_HEADING_TYPES = frozenset({"/H1", "/H2", "/H3", "/H4", "/H5", "/H6"})


def pdf_lbody_has_text_child_blocks(doc: Any, lbody_xref: int) -> bool:
    """True when /LBody contains nested /P or /L (text lives in those descendants)."""
    for cx in pdf_get_child_struct_xrefs(doc, lbody_xref):
        st = pdf_get_struct_type(doc, cx)
        if st in ("/P", "/L", *PDF_HEADING_TYPES):
            return True
    return False


def pdf_is_taggable_content_struct(doc: Any, xref: int, struct_type: Optional[str]) -> bool:
    """Whether a structure node should be extracted as an editable language block."""
    if struct_type in PDF_CONTENT_BLOCK_TYPES:
        return True
    if struct_type == PDF_LBODY_STRUCT_TYPE:
        return not pdf_lbody_has_text_child_blocks(doc, xref)
    return False


def _parse_xref_ref(val: str) -> Optional[int]:
    if not val:
        return None
    m = re.match(r"(\d+)\s+\d+\s+R", val.strip())
    if m:
        return int(m.group(1))
    try:
        return int(val.strip())
    except ValueError:
        return None


def pdf_has_struct_tree(doc: Any) -> bool:
    """Return True when the PDF catalog has a StructTreeRoot."""
    try:
        catalog_xref = doc.pdf_catalog()
        st_type, _ = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        return st_type == "xref"
    except Exception:
        return False


def pdf_struct_tree_root_xref(doc: Any) -> Optional[int]:
    if not pdf_has_struct_tree(doc):
        return None
    try:
        catalog_xref = doc.pdf_catalog()
        st_type, st_val = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if st_type == "xref":
            return _parse_xref_ref(st_val)
    except Exception:
        pass
    return None


def pdf_get_struct_type(doc: Any, xref: int) -> Optional[str]:
    try:
        s_type, s_val = doc.xref_get_key(xref, "S")
        if s_type == "name":
            return s_val
    except Exception:
        pass
    return None


def pdf_parse_lang_value(raw: str) -> str:
    """Normalize a PDF Lang string value to a BCP47-ish code (primary subtag lower)."""
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    elif text.startswith("<") and text.endswith(">"):
        hex_body = text[1:-1]
        if hex_body.startswith("FEFF"):
            try:
                text = bytes.fromhex(hex_body[4:]).decode("utf-16-be", errors="replace")
            except Exception:
                text = ""
        else:
            try:
                text = bytes.fromhex(hex_body).decode("utf-16-be", errors="replace")
            except Exception:
                text = ""
    text = text.replace("\\(", "(").replace("\\)", ")")
    if not text:
        return ""
    return text.split("-")[0].lower()


def pdf_get_lang(doc: Any, xref: int) -> Optional[str]:
    try:
        lang_type, lang_val = doc.xref_get_key(xref, "Lang")
        if lang_type == "string":
            parsed = pdf_parse_lang_value(lang_val)
            return parsed or None
    except Exception:
        pass
    return None


def pdf_format_lang_value(lang_code: str) -> str:
    """Format a language code as a PDF string object value for xref_set_key."""
    code = (lang_code or "").strip()
    if not code:
        return "()"
    escaped = code.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped})"


def pdf_set_lang(doc: Any, xref: int, lang_code: Optional[str]) -> None:
    """Set or remove /Lang on a structure element."""
    if lang_code:
        doc.xref_set_key(xref, "Lang", pdf_format_lang_value(lang_code))
    else:
        try:
            doc.xref_set_key(xref, "Lang", "null")
        except Exception:
            pass


def pdf_set_actual_text(doc: Any, xref: int, text: Optional[str]) -> None:
    """Set or remove /ActualText on a structure element (used for span read-back)."""
    if text:
        doc.xref_set_key(xref, "ActualText", pdf_format_lang_value(text))
    else:
        try:
            doc.xref_set_key(xref, "ActualText", "null")
        except Exception:
            pass


def pdf_get_page_xref_for_struct(doc: Any, xref: int, inherited_page_xref: Optional[int] = None) -> Optional[int]:
    try:
        pg_type, pg_val = doc.xref_get_key(xref, "Pg")
        if pg_type == "xref":
            parsed = _parse_xref_ref(pg_val)
            if parsed is not None:
                return parsed
    except Exception:
        pass
    return inherited_page_xref


def pdf_page_index_from_xref(doc: Any, page_xref: Optional[int]) -> Optional[int]:
    if page_xref is None:
        return None
    for i in range(len(doc)):
        if doc[i].xref == page_xref:
            return i
    return None


def pdf_get_page_for_struct(
    doc: Any,
    xref: int,
    inherited_page_xref: Optional[int] = None,
) -> Optional[int]:
    page_xref = pdf_get_page_xref_for_struct(doc, xref, inherited_page_xref)
    return pdf_page_index_from_xref(doc, page_xref)


def pdf_get_child_struct_xrefs(doc: Any, parent_xref: int) -> List[int]:
    """Return child structure element xrefs (/S is not null)."""
    try:
        k_type, k_val = doc.xref_get_key(parent_xref, "K")
    except Exception:
        return []
    if k_type == "null":
        return []
    candidate_xrefs: List[int] = []
    if k_type == "xref":
        parsed = _parse_xref_ref(k_val)
        if parsed is not None:
            candidate_xrefs.append(parsed)
    elif k_type == "array":
        for r in re.findall(r"(\d+)\s+\d+\s+R", k_val or ""):
            try:
                candidate_xrefs.append(int(r))
            except ValueError:
                pass
    elif k_type == "int":
        return []
    child_xrefs: List[int] = []
    for cxref in candidate_xrefs:
        try:
            cs_type, _ = doc.xref_get_key(cxref, "S")
            if cs_type != "null":
                child_xrefs.append(cxref)
        except Exception:
            pass
    return child_xrefs


def _mcid_from_mcr_xref(doc: Any, mcr_xref: int) -> Optional[int]:
    try:
        mcid_type, mcid_val = doc.xref_get_key(mcr_xref, "MCID")
        if mcid_type == "int":
            return int(mcid_val)
    except Exception:
        pass
    return None


def _tokenize_k_array(val: str) -> List[str]:
    """Split a PDF array string into xref refs and bare integer tokens in order."""
    text = (val or "").strip()
    if text.startswith("["):
        text = text[1:]
    if text.endswith("]"):
        text = text[:-1]
    tokens: List[str] = []
    pos = 0
    while pos < len(text):
        chunk = text[pos:].lstrip()
        if not chunk:
            break
        ref_match = re.match(r"(\d+)\s+\d+\s+R", chunk)
        if ref_match:
            tokens.append(ref_match.group(0))
            pos += len(text[pos:]) - len(chunk) + ref_match.end()
            continue
        int_match = re.match(r"(\d+)", chunk)
        if int_match:
            tokens.append(int_match.group(1))
            pos += len(text[pos:]) - len(chunk) + int_match.end()
            continue
        pos += len(text[pos:]) - len(chunk) + 1
    return tokens


def pdf_collect_mcids_for_struct(doc: Any, xref: int) -> List[int]:
    """
    MCID integers referenced directly by this structure element's /K (in order).

    Child /Span (or other struct) references are skipped; callers walk spans separately.
    """
    try:
        k_type, k_val = doc.xref_get_key(xref, "K")
    except Exception:
        return []
    mcids: List[int] = []

    if k_type == "int":
        try:
            mcids.append(int(k_val))
        except ValueError:
            pass
        return mcids

    if k_type == "xref":
        parsed = _parse_xref_ref(k_val)
        if parsed is not None and pdf_get_struct_type(doc, parsed) is None:
            mcid = _mcid_from_mcr_xref(doc, parsed)
            if mcid is not None:
                mcids.append(mcid)
        return mcids

    if k_type == "array":
        for token in _tokenize_k_array(k_val or ""):
            if re.match(r"\d+\s+\d+\s+R", token):
                cx = int(token.split()[0])
                if pdf_get_struct_type(doc, cx) is not None:
                    continue
                mcid = _mcid_from_mcr_xref(doc, cx)
                if mcid is not None:
                    mcids.append(mcid)
            elif token.isdigit():
                mcids.append(int(token))
        return mcids

    return mcids


KItem = Tuple[str, Any, ...]


def pdf_ordered_k_items(doc: Any, xref: int) -> List[KItem]:
    """
    Structure element /K entries in document order.

    Returns items of the form ("mcid", int) or ("struct", child_xref, struct_type).
    """
    try:
        k_type, k_val = doc.xref_get_key(xref, "K")
    except Exception:
        return []
    items: List[KItem] = []

    if k_type == "int":
        try:
            items.append(("mcid", int(k_val)))
        except ValueError:
            pass
        return items

    if k_type == "xref":
        parsed = _parse_xref_ref(k_val)
        if parsed is None:
            return items
        st = pdf_get_struct_type(doc, parsed)
        if st:
            items.append(("struct", parsed, st))
        else:
            mcid = _mcid_from_mcr_xref(doc, parsed)
            if mcid is not None:
                items.append(("mcid", mcid))
        return items

    if k_type == "array":
        for token in _tokenize_k_array(k_val or ""):
            if re.match(r"\d+\s+\d+\s+R", token):
                cx = int(token.split()[0])
                st = pdf_get_struct_type(doc, cx)
                if st:
                    items.append(("struct", cx, st))
                else:
                    mcid = _mcid_from_mcr_xref(doc, cx)
                    if mcid is not None:
                        items.append(("mcid", mcid))
            elif token.isdigit():
                items.append(("mcid", int(token)))
        return items

    return items


def pdf_get_mcr_xrefs(doc: Any, xref: int) -> List[int]:
    """Return MCR object xrefs referenced by a structure element's /K."""
    try:
        k_type, k_val = doc.xref_get_key(xref, "K")
    except Exception:
        return []
    mcrs: List[int] = []

    def _maybe_add_mcr(cx: int) -> None:
        try:
            t_type, t_val = doc.xref_get_key(cx, "Type")
            if t_type == "name" and t_val == "/MCR":
                mcrs.append(cx)
        except Exception:
            pass

    if k_type == "xref":
        parsed = _parse_xref_ref(k_val)
        if parsed is not None:
            st = pdf_get_struct_type(doc, parsed)
            if st == "/Span":
                mcrs.extend(pdf_get_mcr_xrefs(doc, parsed))
            elif st is None:
                _maybe_add_mcr(parsed)
    elif k_type == "array":
        for r in re.findall(r"(\d+)\s+\d+\s+R", k_val or ""):
            try:
                cx = int(r)
            except ValueError:
                continue
            st = pdf_get_struct_type(doc, cx)
            if st == "/Span":
                mcrs.extend(pdf_get_mcr_xrefs(doc, cx))
            elif st is None:
                _maybe_add_mcr(cx)
    return mcrs


def pdf_get_actual_text(doc: Any, xref: int) -> str:
    try:
        at_type, at_val = doc.xref_get_key(xref, "ActualText")
        if at_type != "string" or not at_val:
            return ""
        text = at_val.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1].replace("\\(", "(").replace("\\)", ")")
        return text
    except Exception:
        pass
    return ""


def pdf_get_primary_language_from_catalog(doc: Any) -> str:
    """Document /Lang from catalog, else metadata, else 'en'."""
    try:
        catalog_xref = doc.pdf_catalog()
        lang_type, lang_val = doc.xref_get_key(catalog_xref, "Lang")
        if lang_type == "string":
            parsed = pdf_parse_lang_value(lang_val)
            if parsed:
                return parsed
    except Exception:
        pass
    try:
        meta = doc.metadata or {}
        for key in ("language", "lang"):
            val = (meta.get(key) or "").strip()
            if val:
                return val.split("-")[0].lower()
    except Exception:
        pass
    return "en"


def pdf_set_catalog_lang(doc: Any, lang_code: str) -> None:
    if not lang_code:
        return
    try:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", pdf_format_lang_value(lang_code))
    except Exception:
        pass
