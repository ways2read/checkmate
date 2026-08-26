"""Heuristic MathML quality checks (Nordic guidelines), after Nu schema."""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import CheckResult, Issue, Severity, Verdict
from .publication import HTML_SUFFIXES, MATHML_SUFFIXES, is_html_url

MATHML_QUALITY_DISPLAY_NAME = "MathML quality"
MATHML_GUIDELINES_TITLE = "Nordic MathML Guidelines"
MATHML_GUIDELINES_URL = (
    "https://github.com/nlbdev/mathml-guidelines/blob/main/"
    "Nordic%20MathML%20Guidelines.md"
)
MATHML_CORE_URL = "https://www.w3.org/TR/mathml-core/"
DAISY_KB_MATHML_URL = "https://kb.daisy.org/publishing/docs/html/mathml.html"

MATH_NS = "http://www.w3.org/1998/Math/MathML"
_PACKAGE_SUFFIXES = {".epub", ".ebrl", ".zip"}
_MARKUP_SCAN_SUFFIXES = HTML_SUFFIXES | MATHML_SUFFIXES
# ASCII hyphen, en dash, em dash — not the math minus U+2212.
_ASCII_DASHES = ("-", "\u2013", "\u2014")
_ROOT_SYMBOLS = ("\u221a", "\u221b", "\u221c")  # √ ∛ ∜
_MACRON = "\u0304"

_MATH_OPEN_RE = re.compile(r"<math\b", re.IGNORECASE)

_TOKEN_EMPTY = frozenset({"mo", "mi", "mn"})
_SCRIPT_PARENTS = frozenset({"msup", "msub", "msubsup"})
_INVISIBLE_TIMES = "\u2062"
_FUNCTION_APPLICATION = "\u2061"
_INVISIBLE_PLUS = "\u2064"
_INVISIBLE_OPS = frozenset(
    {_INVISIBLE_TIMES, _FUNCTION_APPLICATION, _INVISIBLE_PLUS, "\u2063"}
)
_PUNCT_CHARS = frozenset(".,;:")
_NAMED_FUNCTIONS = frozenset(
    {
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "asin",
        "acos",
        "atan",
        "arcsin",
        "arccos",
        "arctan",
        "sinh",
        "cosh",
        "tanh",
        "ln",
        "lg",
        "log",
        "lim",
        "max",
        "min",
        "exp",
        "det",
        "gcd",
        "lcm",
        "arg",
        "deg",
    }
)
_SEQUENCE_PARENTS = frozenset({"math", "mrow", "mtd", "mstyle", "mpadded"})
_OCR_FUNC_MAP = {
    "1n": "ln",
    "In": "ln",
    "1g": "lg",
    "Ig": "lg",
    "1og": "log",
    "Iog": "log",
    "1im": "lim",
    "Iim": "lim",
}
_MTEXT_OPERATORS = frozenset("+−±∓×÷⋅=<>≤≥≠≈*")
_UNIT_RE = re.compile(
    r"^(m|cm|mm|km|g|kg|mg|s|ms|min|h|l|ml|n|pa|hz|nm|kn|mol)$",
    re.IGNORECASE,
)
_NUMBERISH_RE = re.compile(r"^[\d\s.,\u00a0]+$")
_CONTENT_MATHML = frozenset(
    {
        "apply",
        "reln",
        "fn",
        "interval",
        "ci",
        "cn",
        "csymbol",
        "plus",
        "minus",
        "times",
        "divide",
        "power",
        "eq",
        "neq",
        "gt",
        "lt",
        "geq",
        "leq",
        "and",
        "or",
        "not",
        "xor",
        "sum",
        "product",
        "limit",
        "log",
        "sin",
        "cos",
        "tan",
        "exp",
        "abs",
        "forall",
        "exists",
        "lambda",
        "int",
        "diff",
        "partialdiff",
        "bvar",
        "lowlimit",
        "uplimit",
        "degree",
        "condition",
        "declare",
        "approx",
        "set",
        "list",
        "vector",
        "matrix",
        "matrixrow",
    }
)
_APPLY_FOLLOWERS = frozenset(
    {"mn", "mi", "mfrac", "msqrt", "mroot", "msup", "msub", "mrow"}
)


def _local(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _line_at(text: str, index: int) -> int:
    if index < 0:
        return 1
    return text.count("\n", 0, index) + 1


def _location(path: Path | None, text: str, index: int) -> str:
    line = _line_at(text, index)
    if path is not None:
        return f"{path}:{line}"
    return str(line)


def _direct_text(el: ET.Element) -> str:
    return el.text or ""


def _is_empty_token(el: ET.Element) -> bool:
    if list(el):
        return False
    return not _direct_text(el).strip()


def _stripped(el: ET.Element) -> str:
    return _direct_text(el).strip()


def _is_invisible_op(el: ET.Element) -> bool:
    return _local(el.tag) == "mo" and _stripped(el) in _INVISIBLE_OPS


def _is_open_paren(el: ET.Element) -> bool:
    return _local(el.tag) == "mo" and _stripped(el) in {"(", "[", "{"}


def _is_one_letter_mi(el: ET.Element) -> bool:
    if _local(el.tag) != "mi" or list(el):
        return False
    text = _stripped(el)
    return len(text) == 1 and text.isalpha()


def _is_named_function_mi(el: ET.Element) -> bool:
    if _local(el.tag) != "mi":
        return False
    return _stripped(el).lower() in _NAMED_FUNCTIONS


def _is_fgh(el: ET.Element) -> bool:
    return _local(el.tag) == "mi" and _stripped(el) in {"f", "g", "h"}


def _needs_function_arg(el: ET.Element) -> bool:
    if _is_open_paren(el):
        return True
    return _local(el.tag) in _APPLY_FOLLOWERS and _local(el.tag) != "mrow"


def _attr(el: ET.Element, name: str) -> str:
    if name in el.attrib:
        return str(el.attrib.get(name) or "")
    for key, value in el.attrib.items():
        if _local(str(key)) == name:
            return str(value or "")
    return ""


def _table_cell_count(el: ET.Element) -> int:
    return sum(1 for child in el.iter() if _local(child.tag) == "mtd")


def _find_tag_index(haystack: str, local: str, start: int) -> int:
    low = haystack.lower()
    needle = f"<{local.lower()}"
    idx = low.find(needle, start)
    if idx >= 0:
        return idx
    prefixed = f":{local.lower()}"
    idx = low.find(prefixed, start)
    if idx < 0:
        return -1
    lt = haystack.rfind("<", start, idx + 1)
    return lt if lt >= start else idx


def _help_fields() -> dict[str, str]:
    return {
        "help_url": MATHML_GUIDELINES_URL,
        "help_title": MATHML_GUIDELINES_TITLE,
        "help_text": (
            "Heuristic check against the Nordic MathML Guidelines. "
            "Some hits are false positives; confirm against the expression."
        ),
    }


def _issue(
    *,
    code: str,
    message: str,
    location: str,
) -> Issue:
    help_fields = _help_fields()
    return Issue(
        severity=Severity.WARNING,
        code=code,
        message=message,
        location=location,
        source=MATHML_QUALITY_DISPLAY_NAME,
        help_url=help_fields["help_url"],
        help_title=help_fields["help_title"],
        help_text=help_fields["help_text"],
        ruleset="Nordic MathML Guidelines",
    )


def _scan_siblings(parent: ET.Element, *, location: str) -> list[Issue]:
    """Nordic sequence heuristics on consecutive children of *parent*."""
    issues: list[Issue] = []
    kids = list(parent)
    run: list[ET.Element] = []
    for a, b in zip(kids, kids[1:]):
        a_local = _local(a.tag)
        b_local = _local(b.tag)
        if _is_invisible_op(a) or _is_invisible_op(b):
            run = []
            continue
        if a_local == "mn" and (b_local == "mn"):
            issues.append(
                _issue(
                    code="mathml-split-number",
                    message=(
                        "Number looks split across adjacent mn elements. "
                        "Keep decimal/thousands separators inside one mn."
                    ),
                    location=location,
                )
            )
        if a_local == "mtext" and b_local == "mtext":
            issues.append(
                _issue(
                    code="mathml-adjacent-mtext",
                    message="Adjacent mtext elements. Merge them, or use HTML for commentary.",
                    location=location,
                )
            )
        if a_local == "mn" and (_is_open_paren(b) or b_local == "mi"):
            issues.append(
                _issue(
                    code="mathml-invisible-times",
                    message=(
                        "Missing invisible times (U+2062) between a number and "
                        "the following identifier or parenthesis."
                    ),
                    location=location,
                )
            )
        if _is_one_letter_mi(a) and _is_one_letter_mi(b):
            issues.append(
                _issue(
                    code="mathml-invisible-times",
                    message=(
                        "Missing invisible times (U+2062) between adjacent "
                        "one-letter mi elements."
                    ),
                    location=location,
                )
            )
        if a_local == "mfrac" and b_local == "msup":
            issues.append(
                _issue(
                    code="mathml-invisible-times",
                    message="Missing invisible times (U+2062) between mfrac and msup.",
                    location=location,
                )
            )
        if (_is_named_function_mi(a) or _is_fgh(a)) and _needs_function_arg(b):
            issues.append(
                _issue(
                    code="mathml-function-apply",
                    message=(
                        "Missing invisible function application (U+2061) after "
                        "a function name."
                    ),
                    location=location,
                )
            )
        if a_local == "mn" and b_local == "mfrac":
            issues.append(
                _issue(
                    code="mathml-invisible-plus",
                    message=(
                        "Missing invisible plus (U+2064) for a mixed number "
                        "(whole number followed by a fraction)."
                    ),
                    location=location,
                )
            )
        if a_local == "mn" and b_local == "msup":
            base = list(b)[0] if list(b) else None
            if (
                _stripped(a) == "1"
                and base is not None
                and _local(base.tag) == "mn"
                and _stripped(base) == "0"
            ):
                issues.append(
                    _issue(
                        code="mathml-msup-base",
                        message=(
                            "msup base looks like 0 after a 1 (1 0⁴). "
                            "The base is probably 10, not 0."
                        ),
                        location=location,
                    )
                )

    for kid in kids:
        if _is_one_letter_mi(kid):
            run.append(kid)
            if len(run) == 5:
                issues.append(
                    _issue(
                        code="mathml-letter-mi-run",
                        message=(
                            "Five or more adjacent one-letter mi elements. "
                            "This is often words that should be mtext."
                        ),
                        location=location,
                    )
                )
        else:
            run = []
    return issues


def _scan_html_context(el: ET.Element, *, location: str) -> list[Issue]:
    """Space-between-math / adjacent-math checks on HTML parents."""
    issues: list[Issue] = []
    kids = list(el)
    for a, b in zip(kids, kids[1:]):
        if _local(a.tag) == "math" and _local(b.tag) == "math":
            issues.append(
                _issue(
                    code="mathml-adjacent-math",
                    message=(
                        "Two math elements next to each other. Put a space "
                        "between them, or join them in one math element."
                    ),
                    location=location,
                )
            )
        if _local(b.tag) == "math":
            tail = a.tail or ""
            if tail and tail[-1].isalnum():
                issues.append(
                    _issue(
                        code="mathml-adjacent-text",
                        message=(
                            "Letter or digit immediately before math, with no "
                            "space. Nordic guidelines want a space between text "
                            "and MathML."
                        ),
                        location=location,
                    )
                )
            elif not tail and _local(a.tag) != "math":
                # Element glued to math: <span>see</span><math>
                prev_text = "".join(a.itertext())
                if prev_text and prev_text[-1].isalnum():
                    issues.append(
                        _issue(
                            code="mathml-adjacent-text",
                            message=(
                                "Letter or digit immediately before math, with no "
                                "space. Nordic guidelines want a space between text "
                                "and MathML."
                            ),
                            location=location,
                        )
                    )
        if _local(a.tag) == "math":
            tail = a.tail or ""
            if tail and tail[0].isalnum():
                issues.append(
                    _issue(
                        code="mathml-adjacent-text",
                        message=(
                            "Letter or digit immediately after math, with no "
                            "space. Nordic guidelines want a space between "
                            "MathML and text."
                        ),
                        location=location,
                    )
                )
    return issues


def _scan_element(
    el: ET.Element,
    *,
    text: str,
    path: Path | None,
    search_at: int,
    inside_math: bool,
) -> tuple[list[Issue], int]:
    issues: list[Issue] = []
    local = _local(el.tag)
    tag_at = _find_tag_index(text, local, search_at)
    loc_index = tag_at if tag_at >= 0 else search_at
    next_search = (tag_at + 1) if tag_at >= 0 else search_at
    now_inside = inside_math or local == "math"
    location = _location(path, text, loc_index)

    if now_inside:
        if local == "mfenced":
            issues.append(
                _issue(
                    code="mathml-mfenced",
                    message=(
                        "Deprecated mfenced. Use mrow with mo fences instead "
                        "(MathML Core / Nordic guidelines)."
                    ),
                    location=location,
                )
            )

        if local == "mo":
            body = _direct_text(el).strip()
            if (
                body
                and any(dash in body for dash in _ASCII_DASHES)
                and "\u2212" not in body
            ):
                issues.append(
                    _issue(
                        code="mathml-hyphen-minus",
                        message=(
                            "Use math minus − (U+2212) in mo, not hyphen-minus, "
                            "en dash, or em dash."
                        ),
                        location=location,
                    )
                )

        if local in {"mi", "mtext"} and _MACRON in "".join(el.itertext()):
            issues.append(
                _issue(
                    code="mathml-macron",
                    message=(
                        "Combining macron (U+0304) in mi/mtext. Use mover for "
                        "a bar over a symbol."
                    ),
                    location=location,
                )
            )

        combined = (
            "".join(el.itertext()) if local in {"mo", "mi", "mn", "mtext"} else ""
        )
        if any(sym in combined for sym in _ROOT_SYMBOLS):
            issues.append(
                _issue(
                    code="mathml-unicode-root",
                    message=(
                        "Unicode root symbol (√, ∛, or ∜). Use msqrt or mroot "
                        "so the radicand is in the markup."
                    ),
                    location=location,
                )
            )

        if local in _TOKEN_EMPTY and _is_empty_token(el):
            issues.append(
                _issue(
                    code="mathml-empty",
                    message=f"Empty {local}. Token elements need content.",
                    location=location,
                )
            )

        if local in _SCRIPT_PARENTS:
            children = list(el)
            labels = {
                "msup": ("base", "exponent"),
                "msub": ("base", "index"),
                "msubsup": ("base", "index", "exponent"),
            }
            names = labels[local]
            for child, name in zip(children, names):
                if _local(child.tag) in _TOKEN_EMPTY and _is_empty_token(child):
                    issues.append(
                        _issue(
                            code="mathml-empty",
                            message=f"Empty {name} in {local}.",
                            location=location,
                        )
                    )

        if local == "math":
            ns_ok = el.tag.startswith("{") and MATH_NS in el.tag
            xmlns = _attr(el, "xmlns")
            if not ns_ok and xmlns.rstrip("/") != MATH_NS:
                issues.append(
                    _issue(
                        code="mathml-namespace",
                        message=(
                            "math should declare xmlns="
                            f'"{MATH_NS}".'
                        ),
                        location=location,
                    )
                )
            start = text[loc_index : loc_index + 24].lower()
            if re.match(r"<m[\w.-]*:math\b", start):
                issues.append(
                    _issue(
                        code="mathml-prefixed-ns",
                        message=(
                            "Prefixed MathML (m:math). Declare the MathML "
                            "namespace on the math element instead."
                        ),
                        location=location,
                    )
                )
            if _attr(el, "alttext") or _attr(el, "altimg"):
                issues.append(
                    _issue(
                        code="mathml-alttext",
                        message=(
                            "Do not use alttext or altimg on math. Support is "
                            "poor; rely on the MathML itself."
                        ),
                        location=location,
                    )
                )
            kids = list(el)
            if len(kids) == 1 and _local(kids[0].tag) == "mrow":
                issues.append(
                    _issue(
                        code="mathml-outer-mrow",
                        message=(
                            "math whose only child is mrow. Avoid unnecessary "
                            "outer grouping."
                        ),
                        location=location,
                    )
                )

        if local == "mrow" and len(list(el)) == 1:
            issues.append(
                _issue(
                    code="mathml-singleton-mrow",
                    message="mrow with only one child. Avoid unnecessary grouping.",
                    location=location,
                )
            )

        if local == "mtable" and _table_cell_count(el) == 1:
            issues.append(
                _issue(
                    code="mathml-mtable-one-cell",
                    message=(
                        "mtable with only one cell. Use mrow unless this is "
                        "really a table."
                    ),
                    location=location,
                )
            )

        if local in {"semantics", "annotation", "annotation-xml"}:
            issues.append(
                _issue(
                    code="mathml-semantics",
                    message=(
                        "semantics/annotation markup. Nordic production does "
                        "not use these unless the Ordering Agency asks."
                    ),
                    location=location,
                )
            )

        if local in _CONTENT_MATHML:
            issues.append(
                _issue(
                    code="mathml-content",
                    message=(
                        f"Content MathML element {local}. Nordic production "
                        "uses presentation MathML unless specified."
                    ),
                    location=location,
                )
            )

        if local == "mo":
            body = _stripped(el)
            if body in _PUNCT_CHARS:
                issues.append(
                    _issue(
                        code="mathml-punct-mo",
                        message=(
                            "Sentence punctuation in mo. Nordic guidelines "
                            "wrap period, comma, and colon in mtext."
                        ),
                        location=location,
                    )
                )
            if "'" in body or "\u0027" in body:
                issues.append(
                    _issue(
                        code="mathml-prime",
                        message=(
                            "ASCII apostrophe in mo. Use prime ′ (U+2032) for "
                            "derivatives."
                        ),
                        location=location,
                    )
                )

        if local == "mn":
            body = _direct_text(el)
            if "  " in body or "\u00a0\u00a0" in body:
                issues.append(
                    _issue(
                        code="mathml-mn-spaces",
                        message="mn contains multiple consecutive spaces.",
                        location=location,
                    )
                )

        if local == "mtext":
            body = _stripped(el)
            low = body.lower()
            if len(body) == 1 and body.isalpha():
                issues.append(
                    _issue(
                        code="mathml-mtext-letter",
                        message="Single letter in mtext. Use mi for identifiers.",
                        location=location,
                    )
                )
            if low in _NAMED_FUNCTIONS:
                issues.append(
                    _issue(
                        code="mathml-mtext-function",
                        message=(
                            f"Function name {body!r} in mtext. Use mi "
                            "(or mo for lim)."
                        ),
                        location=location,
                    )
                )
            if body in _MTEXT_OPERATORS:
                issues.append(
                    _issue(
                        code="mathml-mtext-operator",
                        message="Operator in mtext. Use mo.",
                        location=location,
                    )
                )
            if _UNIT_RE.match(body):
                issues.append(
                    _issue(
                        code="mathml-mtext-unit",
                        message="Unit in mtext. Use mi (mathvariant=normal).",
                        location=location,
                    )
                )
            if body and _NUMBERISH_RE.match(body) and re.search(r"\d", body):
                issues.append(
                    _issue(
                        code="mathml-mtext-number",
                        message="Number in mtext. Use mn.",
                        location=location,
                    )
                )

        if local in {"mi", "mtext"}:
            raw_token = _stripped(el)
            mapped = _OCR_FUNC_MAP.get(raw_token)
            if mapped:
                issues.append(
                    _issue(
                        code="mathml-ocr-function",
                        message=(
                            f"Token {raw_token!r} looks like OCR for {mapped}. "
                            "Check ln/lg/log/lim."
                        ),
                        location=location,
                    )
                )
            if local == "mi" and raw_token.lower() == "lim":
                issues.append(
                    _issue(
                        code="mathml-lim-mo",
                        message="lim in mi. Nordic guidelines mark lim as mo.",
                        location=location,
                    )
                )

        if local == "msup" and len(list(el)) >= 2:
            exp = list(el)[1]
            if _local(exp.tag) == "mi" and _stripped(exp) in {"o", "O"}:
                issues.append(
                    _issue(
                        code="mathml-ocr-exponent",
                        message=(
                            "msup exponent is the letter o/O. This is often "
                            "OCR for 0."
                        ),
                        location=location,
                    )
                )

        if local in {"mi", "mo"} and "'" in _stripped(el):
            if local == "mi":
                issues.append(
                    _issue(
                        code="mathml-prime",
                        message=(
                            "ASCII apostrophe in mi. Use prime ′ (U+2032) for "
                            "derivatives."
                        ),
                        location=location,
                    )
                )

    for child in list(el):
        child_issues, next_search = _scan_element(
            child,
            text=text,
            path=path,
            search_at=next_search,
            inside_math=now_inside,
        )
        issues.extend(child_issues)
    if now_inside and local in _SEQUENCE_PARENTS:
        issues.extend(_scan_siblings(el, location=location))
    elif not now_inside:
        issues.extend(_scan_html_context(el, location=location))
    return issues, next_search


def _iter_math_fragments(text: str) -> list[tuple[int, str]]:
    """Return (start_index, fragment) for each top-level math element."""
    fragments: list[tuple[int, str]] = []
    lower = text.lower()
    i = 0
    n = len(text)
    while i < n:
        match = _MATH_OPEN_RE.search(text, i)
        if match is None:
            break
        start = match.start()
        pos = match.end()
        depth = 1
        while depth and pos < n:
            nxt_open = lower.find("<math", pos)
            nxt_close = lower.find("</math", pos)
            if nxt_close < 0:
                return fragments
            if 0 <= nxt_open < nxt_close:
                end_gt = lower.find(">", nxt_open)
                if end_gt < 0:
                    return fragments
                depth += 1
                pos = end_gt + 1
                continue
            end_gt = lower.find(">", nxt_close)
            if end_gt < 0:
                return fragments
            depth -= 1
            pos = end_gt + 1
            if depth == 0:
                fragments.append((start, text[start:pos]))
                i = pos
                break
        else:
            break
    return fragments


def _parse_root(fragment: str) -> ET.Element | None:
    try:
        return ET.fromstring(fragment)
    except ET.ParseError:
        return None


def _scan_tree(root: ET.Element, *, text: str, path: Path | None) -> list[Issue]:
    return _scan_element(
        root,
        text=text,
        path=path,
        search_at=0,
        inside_math=_local(root.tag) == "math",
    )[0]


def issues_from_mathml_text(text: str, *, path: Path | None = None) -> list[Issue]:
    """Scan MathML or HTML text for quality warnings. Empty if there is no math."""
    raw = text or ""
    if "<math" not in raw.lower():
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        root = None
    if root is not None:
        return _dedupe_issues(_scan_tree(root, text=raw, path=path))

    issues: list[Issue] = []
    for start, frag in _iter_math_fragments(raw):
        tree = _parse_root(frag)
        if tree is None:
            continue
        frag_issues = _scan_tree(tree, text=frag, path=None)
        base_line = _line_at(raw, start)
        for issue in frag_issues:
            local_line = int(issue.location) if issue.location.isdigit() else 1
            line = base_line + local_line - 1
            issue.location = f"{path}:{line}" if path is not None else str(line)
        issues.extend(frag_issues)
    return _dedupe_issues(issues)


def _dedupe_issues(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Issue] = []
    for issue in issues:
        key = (issue.code, issue.location, issue.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _relocate_issues(issues: list[Issue], prefix: str) -> list[Issue]:
    """Point issue locations at *prefix* (zip member or relative path)."""
    for issue in issues:
        loc = (issue.location or "").strip()
        issue.location = prefix if not loc else f"{prefix}:{loc}"
    return issues


def _looks_like_math_bytes(raw: bytes) -> bool:
    low = raw.lower()
    return b"<math" in low or b":math" in low


def issues_from_package(path: Path) -> list[Issue]:
    """Scan HTML/XHTML/MathML members inside a packaged EPUB/ZIP."""
    collected: list[Issue] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = sorted(zf.namelist())
            for name in names:
                member = name.replace("\\", "/")
                if member.endswith("/") or member.startswith("__MACOSX/"):
                    continue
                suffix = Path(member).suffix.lower()
                if suffix not in _MARKUP_SCAN_SUFFIXES:
                    continue
                try:
                    raw = zf.read(name)
                except (KeyError, OSError, RuntimeError):
                    continue
                if not _looks_like_math_bytes(raw):
                    continue
                text = raw.decode("utf-8", errors="replace")
                collected.extend(
                    _relocate_issues(issues_from_mathml_text(text), member)
                )
    except (OSError, zipfile.BadZipFile):
        return []
    return collected


def issues_from_path(path: Path) -> list[Issue]:
    """Scan a file, packaged EPUB, or HTML/XHTML files in a folder."""
    path = path.expanduser().resolve()
    if path.is_file():
        if path.suffix.lower() in _PACKAGE_SUFFIXES:
            return issues_from_package(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return issues_from_mathml_text(text, path=path)
    if not path.is_dir():
        return []
    collected: list[Issue] = []
    try:
        for child in sorted(path.rglob("*")):
            if not child.is_file() or child.suffix.lower() not in _MARKUP_SCAN_SUFFIXES:
                continue
            collected.extend(issues_from_path(child))
    except OSError:
        return collected
    return collected


def _counts_from_issues(issues: list[Issue]) -> dict[str, int]:
    counts = {
        "fatals": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "usages": 0,
    }
    for issue in issues:
        if issue.severity == Severity.FATAL:
            counts["fatals"] += 1
        elif issue.severity == Severity.ERROR:
            counts["errors"] += 1
        elif issue.severity == Severity.WARNING:
            counts["warnings"] += 1
        elif issue.severity == Severity.INFO:
            counts["infos"] += 1
        elif issue.severity == Severity.USAGE:
            counts["usages"] += 1
    return counts


def merge_mathml_quality(result: CheckResult, issues: list[Issue]) -> CheckResult:
    """Append quality warnings and refresh counts / verdict / source filter."""
    if not issues:
        return result
    result.issues.extend(issues)
    counts = _counts_from_issues(result.issues)
    result.fatals = counts["fatals"]
    result.errors = counts["errors"]
    result.warnings = counts["warnings"]
    result.infos = counts["infos"]
    result.usages = counts["usages"]
    if result.verdict == Verdict.PASSED and result.warnings:
        result.verdict = Verdict.PASSED_WITH_WARNINGS
    quality_counts = _counts_from_issues(issues)
    if result.source_counts:
        existing = {name for name, _ in result.source_counts}
        if MATHML_QUALITY_DISPLAY_NAME not in existing:
            result.source_counts.append(
                (MATHML_QUALITY_DISPLAY_NAME, quality_counts)
            )
    extra = {label for label, _ in result.extra_meta}
    if "MathML quality" not in extra:
        result.extra_meta.append(
            ("MathML quality", "Nordic guidelines (heuristics)")
        )
    name = (result.tool_name or "").strip()
    if name and "mathml quality" not in name.lower():
        result.tool_name = f"{name} + MathML quality"
    if result.raw_log:
        result.raw_log = (
            result.raw_log.rstrip()
            + f"\n--- {MATHML_QUALITY_DISPLAY_NAME} ---\n"
            + f"{len(issues)} warning(s)"
        )
    else:
        result.raw_log = f"{MATHML_QUALITY_DISPLAY_NAME}: {len(issues)} warning(s)"
    return result


def attach_mathml_quality(
    result: CheckResult,
    target: str,
    *,
    extra_paths: list[Path] | None = None,
    enabled: bool | None = None,
) -> CheckResult:
    """Run the quality pass on a local file, folder, or packaged EPUB.

    When *extra_paths* is a non-empty list, only those files are scanned.
    An empty list falls back to *target* (same as omitting *extra_paths*).
    Off unless *enabled* is true or the Nordic guidelines setting is on.
    """
    if enabled is None:
        from .settings import mathml_nordic_guidelines

        enabled = mathml_nordic_guidelines()
    if not enabled:
        return result
    text = (target or "").strip().strip('"')
    if extra_paths:
        paths = list(extra_paths)
    else:
        paths = []
        if text and not is_html_url(text):
            try:
                path = Path(text).expanduser().resolve()
            except (OSError, ValueError):
                path = None
            else:
                if path.exists():
                    paths.append(path)
    seen: set[Path] = set()
    issues: list[Issue] = []
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        issues.extend(issues_from_path(resolved))
    return merge_mathml_quality(result, _dedupe_issues(issues))
