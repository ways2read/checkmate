#!/usr/bin/env python3
"""Create a minimal valid EPUB 3 for local CheckMate testing.

Not shipped; used only to produce a sample publication under testdata/.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:11111111-1111-1111-1111-111111111111</dc:identifier>
    <dc:title>CheckMate Sample</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
  </spine>
</package>
"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head><title>Contents</title></head>
<body>
<nav epub:type="toc">
<h1>Contents</h1>
<ol><li><a href="chapter1.xhtml">Chapter 1</a></li></ol>
</nav>
</body>
</html>
"""

CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head><title>Chapter 1</title></head>
<body>
<h1>Chapter 1</h1>
<p>Hello from a CheckMate sample publication.</p>
</body>
</html>
"""


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("testdata/sample.epub")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("EPUB/package.opf", OPF)
        zf.writestr("EPUB/nav.xhtml", NAV)
        zf.writestr("EPUB/chapter1.xhtml", CHAPTER)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
