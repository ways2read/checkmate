"""Ace rule id → DAISY Accessible Publishing Knowledge Base article."""

from __future__ import annotations

# Sourced from Ace's axe-rules-kb-mapping.js (daisy/ace-report-axe).
# EPUB-specific Ace rules added from ace-core checker-epub.js.
KB_PUBLISHING_BASE = "https://kb.daisy.org/publishing/"

# rule_id -> (learn_more_title, path relative to KB_PUBLISHING_BASE)
ACE_RULE_KB_PATHS: dict[str, tuple[str, str]] = {
    'accesskeys': ('DAISY KB: Accesskeys', 'docs/html/accesskeys.html'),
    'area-alt': ('DAISY KB: Image Maps', 'docs/html/maps.html'),
    'aria-allowed-attr': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-allowed-role': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-braille-equivalent': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-command-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-conditional-attr': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-deprecated-role': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-dialog-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-hidden-body': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-hidden-focus': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-input-field-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-meter-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-progressbar-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-prohibited-attr': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-required-attr': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-required-children': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-required-parent': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-roledescription': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-roles': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'aria-toggle-field-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-tooltip-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-treeitem-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-valid-attr-value': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'aria-valid-attr': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'audio-caption': ('DAISY KB: Audio', 'docs/html/audio.html'),
    'autocomplete-valid': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'avoid-inline-spacing': ('DAISY KB: Visual Separation', 'docs/html/separation.html'),
    'blink': ('DAISY KB: Visual Separation', 'docs/html/separation.html'),
    'button-name': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'bypass': ('DAISY KB: Sections', 'docs/html/sections.html'),
    'color-contrast-enhanced': ('DAISY KB: Color Contrast', 'docs/css/color.html'),
    'color-contrast': ('DAISY KB: Color Contrast', 'docs/css/color.html'),
    'css-orientation-lock': ('DAISY KB: Visual Separation', 'docs/html/separation.html'),
    'definition-list': ('DAISY KB: Lists', 'docs/html/lists.html'),
    'dlitem': ('DAISY KB: Lists', 'docs/html/lists.html'),
    'document-title': ('DAISY KB: Page Title', 'docs/html/title.html'),
    'duplicate-id-active': ('DAISY KB: Element IDs', 'docs/html/ids.html'),
    'duplicate-id-aria': ('DAISY KB: Element IDs', 'docs/html/ids.html'),
    'duplicate-id': ('DAISY KB: Element IDs', 'docs/html/ids.html'),
    'empty-heading': ('DAISY KB: Headings', 'docs/html/headings.html'),
    'epub-type-has-matching-role': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'focus-order-semantics': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'form-field-multiple-labels': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'frame-focusable-content': ('DAISY KB: Frames / iframes', 'docs/html/iframes.html'),
    'frame-tested': ('DAISY KB: Frames / iframes', 'docs/html/iframes.html'),
    'frame-title-unique': ('DAISY KB: Frames / iframes', 'docs/html/iframes.html'),
    'frame-title': ('DAISY KB: Frames / iframes', 'docs/html/iframes.html'),
    'heading-order': ('DAISY KB: Headings', 'docs/html/headings.html'),
    'hidden-content': ('DAISY KB: Visual Separation', 'docs/html/separation.html'),
    'html-has-lang': ('DAISY KB: Language', 'docs/html/lang.html'),
    'html-lang-valid': ('DAISY KB: Language', 'docs/html/lang.html'),
    'html-xml-lang-mismatch': ('DAISY KB: Language', 'docs/html/lang.html'),
    'identical-links-same-purpose': ('DAISY KB: Links', 'docs/html/links.html'),
    'image-alt': ('DAISY KB: Images', 'docs/html/images.html'),
    'image-redundant-alt': ('DAISY KB: Images', 'docs/html/images.html'),
    'input-button-name': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'input-image-alt': ('DAISY KB: Images', 'docs/html/images.html'),
    'label-content-name-mismatch': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'label-title-only': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'label': ('DAISY KB: Forms', 'docs/html/forms.html'),
    'landmark-banner-is-top-level': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-complementary-is-top-level': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-contentinfo-is-top-level': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-main-is-top-level': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-no-duplicate-banner': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-no-duplicate-contentinfo': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-no-duplicate-main': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-one-main': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'landmark-unique': ('DAISY KB: Landmarks', 'docs/html/landmarks.html'),
    'link-in-text-block': ('DAISY KB: Links', 'docs/html/links.html'),
    'link-name': ('DAISY KB: Links', 'docs/html/links.html'),
    'list': ('DAISY KB: Lists', 'docs/html/lists.html'),
    'listitem': ('DAISY KB: Lists', 'docs/html/lists.html'),
    'marquee': ('DAISY KB: Visual Separation', 'docs/html/separation.html'),
    'meta-refresh-no-exceptions': ('DAISY KB: Document Metadata', 'docs/html/meta.html'),
    'meta-refresh': ('DAISY KB: Document Metadata', 'docs/html/meta.html'),
    'meta-viewport-large': ('DAISY KB: Document Metadata', 'docs/html/meta.html'),
    'meta-viewport': ('DAISY KB: Document Metadata', 'docs/html/meta.html'),
    'nested-interactive': ('DAISY KB: Audio', 'docs/html/audio.html'),
    'no-autoplay-audio': ('DAISY KB: Audio', 'docs/html/audio.html'),
    'object-alt': ('DAISY KB: Objects', 'docs/html/object.html'),
    'p-as-heading': ('DAISY KB: Headings', 'docs/html/headings.html'),
    'page-has-heading-one': ('DAISY KB: Headings', 'docs/html/headings.html'),
    'pagebreak-label': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'presentation-role-conflict': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'region': ('DAISY KB: ARIA Roles', 'docs/html/roles.html'),
    'role-img-alt': ('DAISY KB: Images', 'docs/html/images.html'),
    'scope-attr-valid': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'scrollable-region-focusable': ('DAISY KB: Accesskeys', 'docs/html/accesskeys.html'),
    'select-name': ('DAISY KB: ARIA', 'docs/script/aria.html'),
    'server-side-image-map': ('DAISY KB: Image Maps', 'docs/html/maps.html'),
    'skip-link': ('DAISY KB: Links', 'docs/html/links.html'),
    'summary-name': ('DAISY KB: Images', 'docs/html/images.html'),
    'svg-img-alt': ('DAISY KB: Images', 'docs/html/images.html'),
    'tabindex': ('DAISY KB: Accesskeys', 'docs/html/accesskeys.html'),
    'table-duplicate-name': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'table-fake-caption': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'target-size': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'td-has-header': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'td-headers-attr': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'th-has-data-cells': ('DAISY KB: Tables', 'docs/html/tables.html'),
    'valid-lang': ('DAISY KB: Language', 'docs/html/lang.html'),
    'video-caption': ('DAISY KB: Video', 'docs/html/video.html'),
    'href-no-hash': ('DAISY KB: Links', 'docs/html/links.html'),

    # EPUB-specific Ace rules (ace-core checker-epub.js)
    'metadata-accessmode': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessibilityfeature': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessibilityhazard': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessibilitysummary': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessmodesufficient': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessmode-invalid': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessibilityfeature-invalid': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessibilityhazard-invalid': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-accessmodesufficient-invalid': ('DAISY KB: Schema.org Metadata', 'docs/metadata/schema.org/index.html'),
    'metadata-conformsto-invalid': ('DAISY KB: Evaluation Metadata', 'docs/metadata/evaluation.html'),
    'metadata-accessibilityfeature-printpagenumbers-nopagelist': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'metadata-accessibilityfeature-printpagenumbers-nopagebreaks': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'epub-pagelist-mediaoverlays': ('DAISY KB: Synchronized Media', 'docs/sync-media/index.html'),
    'epub-pagelist-broken': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'epub-pagelist-missing-pagebreak': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'epub-toc-order': ('DAISY KB: Table of Contents', 'docs/navigation/toc.html'),
    'epub-title': ('DAISY KB: Page Title', 'docs/html/title.html'),
    'epub-pagesource': ('DAISY KB: Page List', 'docs/navigation/pagelist.html'),
    'epub-lang': ('DAISY KB: Publication Language', 'docs/epub/language.html'),
}


def normalize_kb_url(url: str) -> str:
    """Prefer https for kb.daisy.org links Ace still emits as http."""
    u = (url or "").strip()
    if u.startswith("http://kb.daisy.org/"):
        return "https://" + u[len("http://"):]
    return u


def kb_resource_for_ace_code(code: str) -> tuple[str, str] | None:
    """Return (title, absolute https URL) for an Ace rule id, or None."""
    key = (code or "").strip().lower()
    if not key:
        return None
    entry = ACE_RULE_KB_PATHS.get(key)
    if not entry:
        return None
    title, rel = entry
    if not rel:
        return None
    return title, normalize_kb_url(KB_PUBLISHING_BASE + rel.lstrip("/"))

