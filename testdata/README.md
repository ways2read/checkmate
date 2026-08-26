# Test data

Put local sample publications here while developing or testing the GUI:

- Packaged eBraille: `*.ebrl`
- Packaged EPUB: `*.epub`
- PDF: `*.pdf` (veraPDF; default profile PDF/UA-2)
- HTML / SVG / CSS / MathML: `*.html`, `*.xhtml`, `*.svg`, `*.css`, `*.mml`
  (optional MathML quality pass: Tools → Settings… → Nordic MathML Guidelines;
  also runs on EPUB HTML/XHTML when that setting is on)
- XML: leftover `*.xml` (DTBook if the root is `<dtbook>`; MathML if it looks like MathML)
- DAISY 2.02: a folder containing `ncc.html`
- DAISY 3 / NIMAS: a folder (or `.opf`) with DAISY package metadata
- Exploded publications: a folder containing a package document
  - eBraille: root `package.opf` with `dc:format` containing `eBraille`
    (e.g. `eBraille 1.0`), plus related files such as `index.html`
  - EPUB: typically `META-INF/container.xml` pointing at the OPF
    (without an eBraille `dc:format`)

These files are ignored by git so copyrighted or private publications are not
committed by mistake.

Example:

```text
testdata/
  my-book.ebrl
  my-book.epub
  my-doc.pdf
  my-exploded-ebraille/
    package.opf
    index.html
    …
  my-exploded-epub/
    META-INF/
      container.xml
    OEBPS/
      content.opf
    …
```
