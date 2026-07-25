# Test data

Put local sample publications here while developing or testing the GUI:

- Packaged eBraille: `*.ebrl`
- Packaged EPUB: `*.epub`
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
