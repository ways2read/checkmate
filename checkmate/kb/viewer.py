"""In-app viewer for offline DAISY Knowledge Base articles."""

from __future__ import annotations

import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import wx

from ..i18n import _, get_language, get_text_direction, language_display_name, LANGUAGES
from .fetch import ensure_article_cached, html_document_for_display, html_to_plain_text
from .store import (
    KbArticleRef,
    en_file_path,
    kb_version_label,
    resolve_local_article,
)
from .translate import TranslationResult, ensure_translation
from .update import update_knowledge_base

# Session preference: show English when a non-English variant exists.
_prefer_english_session = False


def _simple_message_html(message: str) -> str:
    body = message.replace("\n", "<br/>")
    lang = get_language()
    direction = get_text_direction()
    return (
        f'<html lang="{lang}" dir="{direction}"><body><p>'
        f"{body}</p></body></html>"
    )


# Forward Ctrl+PgUp/PgDn out of Edge when the panel is embedded in a notebook.
_KB_PAGE_NAV_SCRIPT = """
<script>
(function () {
  document.addEventListener('keydown', function (e) {
    if (!e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key !== 'PageUp' && e.key !== 'PageDown') return;
    e.preventDefault();
    e.stopPropagation();
    window.location.href = e.key === 'PageUp'
      ? 'checkmate://page-prev'
      : 'checkmate://page-next';
  }, true);
})();
</script>
""".strip()


def _create_webview(parent: wx.Window) -> tuple[wx.Window, bool]:
    try:
        import wx.html2 as html2
    except ImportError:
        html2 = None  # type: ignore

    if html2 is not None:
        backends: list[object] = []
        if sys.platform == "win32":
            backends.append(getattr(html2, "WebViewBackendEdge", None))
        else:
            backends.append(getattr(html2, "WebViewBackendWebKit", None))
        backends.append(None)
        for backend in backends:
            if backend is not None and hasattr(html2.WebView, "IsBackendAvailable"):
                try:
                    if not html2.WebView.IsBackendAvailable(backend):
                        continue
                except Exception:
                    continue
            try:
                if backend is None:
                    view = html2.WebView.New(parent)
                else:
                    view = html2.WebView.New(parent, backend=backend)
            except Exception:
                continue
            if view is None:
                continue
            try:
                view.EnableContextMenu(True)
            except Exception:
                pass
            return view, True

    text = wx.TextCtrl(
        parent,
        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.TE_RICH2,
    )
    return text, False


def _ui_base_lang() -> str:
    return (get_language() or "en").strip().lower().split("-", 1)[0]


class KnowledgeBaseArticlePanel(wx.Panel):
    """Toolbar + WebView for one offline KB article (embeddable)."""

    def __init__(
        self,
        parent: wx.Window,
        *,
        en_rel: str,
        ref: KbArticleRef | None = None,
        on_content_ready: Callable[[], None] | None = None,
        on_page_nav: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._en_rel = en_rel
        self._ref = ref
        self._on_content_ready = on_content_ready
        self._on_page_nav = on_page_nav
        # English UI always shows English; otherwise honour session preference.
        self._showing_english = (
            _prefer_english_session if _ui_base_lang() != "en" else True
        )
        self._content_ready = False
        self._last_html = ""
        self._closing = False
        self._lang_modes: list[str] = ["en"]
        self._build()

    @property
    def content_ready(self) -> bool:
        return self._content_ready and bool((self._last_html or "").strip())

    def online_url(self) -> str:
        url = (self._ref.online_url if self._ref else "") or ""
        if url:
            return url
        from .store import online_url_for_en_rel

        return online_url_for_en_rel(self._en_rel)

    def export_html(self) -> str:
        return self._last_html or ""

    def export_plain(self) -> str:
        return html_to_plain_text(self._last_html or "")

    def start_initial_load(self) -> None:
        """Call after the parent window is shown (Edge needs a live HWND)."""
        if self._closing:
            return
        try:
            if not self:
                return
        except RuntimeError:
            return
        _ensure_article_ui(self, self._en_rel)
        prefer_en = self._showing_english
        self._ref = resolve_local_article(
            self._en_rel, ui_lang=get_language(), prefer_english=prefer_en
        )
        self._realize_view()
        # Non-English UI: translate once (cached) before first paint.
        self._ensure_translation_for_ui()
        self._reload_content()
        self._content_ready = True
        self._notify_ready()

    def load_article(self, en_rel: str, *, prefer_english: bool | None = None) -> None:
        """Switch this panel to another article (in-place navigation)."""
        self._en_rel = en_rel
        if prefer_english is None:
            prefer_english = (
                _prefer_english_session if _ui_base_lang() != "en" else True
            )
        self._showing_english = prefer_english
        self._content_ready = False
        self._last_html = ""
        self._notify_ready()
        if not _ensure_article_ui(self, en_rel):
            msg = _(
                "This Knowledge Base article is not available offline yet.\n"
                "Use Update… to download articles, or Go online to open it in your browser."
            )
            html = _simple_message_html(msg)
            self._show_html(html)
            self._content_ready = True
            self._notify_ready()
            return
        prefer_en = prefer_english
        self._ref = resolve_local_article(
            en_rel, ui_lang=get_language(), prefer_english=prefer_en
        )
        self._ensure_translation_for_ui()
        self._reload_content()
        self._content_ready = True
        self._notify_ready()

    def mark_closing(self) -> None:
        self._closing = True

    def _notify_ready(self) -> None:
        cb = self._on_content_ready
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def _build(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.lang_choice = wx.Choice(self, choices=[])
        self.lang_choice.SetName(_("Article language"))
        self.lang_choice.Bind(wx.EVT_CHOICE, self._on_lang_choice)
        toolbar.Add(self.lang_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.version_label = wx.StaticText(self, label="")
        toolbar.Add(self.version_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.go_online_btn = wx.Button(self, label=_("Go online"))
        self.go_online_btn.Bind(wx.EVT_BUTTON, self._on_go_online)
        toolbar.Add(self.go_online_btn, 0, wx.RIGHT, 8)

        self.update_btn = wx.Button(self, label=_("Update…"))
        self.update_btn.Bind(wx.EVT_BUTTON, self._on_update)
        toolbar.Add(self.update_btn, 0, wx.RIGHT, 8)

        self.translate_btn = wx.Button(self, label=_("Translate…"))
        self.translate_btn.Bind(wx.EVT_BUTTON, self._on_translate)
        toolbar.Add(self.translate_btn, 0)

        root.Add(toolbar, 0, wx.EXPAND | wx.BOTTOM, 8)

        # WebView is created after the parent is visible (Edge paints blank early).
        self.view_host = wx.Panel(self)
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._loading_label = wx.StaticText(self.view_host, label=_("Loading…"))
        host_sizer.Add(self._loading_label, 0, wx.ALL, 8)
        self.view_host.SetSizer(host_sizer)
        self.view = self._loading_label  # placeholder until realized
        self._is_webview = False
        self._view_realized = False
        root.Add(self.view_host, 1, wx.EXPAND)

        self.SetSizer(root)

    def _realize_view(self) -> None:
        """Create the WebView after the host window is visible."""
        if self._view_realized or self._closing:
            return
        host = self.view_host
        view, is_webview = _create_webview(host)
        self.view = view
        self._is_webview = is_webview
        self._view_realized = True
        if is_webview:
            try:
                import wx.html2 as html2

                view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
            except Exception:
                pass
        sizer = host.GetSizer()
        if sizer is None:
            sizer = wx.BoxSizer(wx.VERTICAL)
            host.SetSizer(sizer)
        else:
            sizer.Clear(delete_windows=True)
        sizer.Add(view, 1, wx.EXPAND)
        host.Layout()
        self.Layout()

    def _refresh_ref(self) -> None:
        self._ref = resolve_local_article(
            self._en_rel,
            ui_lang=get_language(),
            prefer_english=self._showing_english,
        )

    def _ensure_translation_for_ui(self) -> None:
        """If the UI language needs an AI translation and cache misses, create it."""
        base = _ui_base_lang()
        if base in ("en", "ja") or self._showing_english:
            return
        self._refresh_ref()
        ref = self._ref
        if ref and ref.translation_path and ref.translation_path.is_file():
            return
        self._run_translation(notify_failure=True)

    def _show_translation_failure(self, tr: TranslationResult) -> None:
        from ..ai.explain import error_message_for_key

        msg = error_message_for_key(tr.error_key, detail=tr.detail or "")
        if not tr.error_key:
            msg = _(
                "Could not translate this article. Check that AI features "
                "are configured, then try again."
            )
        wx.MessageBox(
            msg,
            _("DAISY Knowledge Base"),
            wx.OK | wx.ICON_WARNING,
            self,
        )

    def _translate_with_progress(
        self, lang: str, *, force: bool = False
    ) -> TranslationResult:
        dlg = wx.ProgressDialog(
            _("Translating Knowledge Base article"),
            _("Translating…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )
        result: dict = {"tr": TranslationResult()}

        def worker() -> None:
            try:
                result["tr"] = ensure_translation(self._en_rel, lang, force=force)
            except Exception as exc:  # noqa: BLE001
                result["tr"] = TranslationResult(
                    error_key="provider_error", detail=str(exc)
                )

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while t.is_alive():
            wx.MilliSleep(50)
            try:
                dlg.Pulse(_("Translating…"))
            except Exception:
                pass
            wx.YieldIfNeeded()
        try:
            dlg.Destroy()
        except Exception:
            pass
        return result["tr"]

    def _run_translation(self, *, notify_failure: bool = True) -> bool:
        """Translate the current article into the UI language. Returns success."""
        lang = _ui_base_lang()
        if lang in ("en", "ja"):
            return False
        if not _ensure_article_ui(self, self._en_rel):
            if notify_failure:
                wx.MessageBox(
                    _("Download the English article before translating."),
                    _("DAISY Knowledge Base"),
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return False
        self._refresh_ref()
        if not self._ref or not self._ref.en_path:
            if notify_failure:
                wx.MessageBox(
                    _("Download the English article before translating."),
                    _("DAISY Knowledge Base"),
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return False

        # Already cached and fresh?
        ref = resolve_local_article(
            self._en_rel, ui_lang=get_language(), prefer_english=False
        )
        if ref and ref.translation_path and ref.translation_path.is_file():
            self._showing_english = False
            self._refresh_ref()
            return True

        tr = self._translate_with_progress(lang, force=False)
        if not tr.ok:
            if notify_failure:
                self._show_translation_failure(tr)
            return False
        self._showing_english = False
        self._refresh_ref()
        return True

    def _update_lang_choice(self) -> None:
        """
        Language UI:

        - English UI: hide the choice (always show English).
        - Other UI languages: Translated (default) vs Original English only.
          Japanese UI uses the official JA mirror as the translated page.
        """
        ref = self._ref
        base = _ui_base_lang()
        options: list[tuple[str, str]] = []  # label, mode

        if base == "en":
            self._lang_modes = ["en"]
            self.lang_choice.Clear()
            self.lang_choice.Hide()
            self.translate_btn.Hide()
        else:
            # Translated first (default), then Original English.
            if base == "ja":
                options.append(
                    (
                        _("Translated ({lang})").format(
                            lang=LANGUAGES.get("ja") or language_display_name("ja")
                        ),
                        "ja",
                    )
                )
            else:
                label = _("Translated ({lang})").format(
                    lang=LANGUAGES.get(base) or language_display_name(base)
                )
                options.append((label, "translation"))
            if ref and ref.en_path:
                options.append((_("Original English"), "en"))

            self._lang_modes = [m for _l, m in options]
            self.lang_choice.Set([l for l, _m in options])
            if self._showing_english and "en" in self._lang_modes:
                self.lang_choice.SetSelection(self._lang_modes.index("en"))
            elif "ja" in self._lang_modes:
                self.lang_choice.SetSelection(self._lang_modes.index("ja"))
            elif "translation" in self._lang_modes:
                self.lang_choice.SetSelection(self._lang_modes.index("translation"))
            elif self._lang_modes:
                self.lang_choice.SetSelection(0)

            show_choice = len(options) > 1
            self.lang_choice.Show(show_choice)

            # Retranslate when a cached translation exists; otherwise auto-translate
            # on open covers the first pass.
            has_tr = bool(
                ref and ref.translation_path and ref.translation_path.is_file()
            )
            can_translate = base not in ("en", "ja") and bool(ref and ref.en_path)
            self.translate_btn.Show(can_translate)
            self.translate_btn.Enable(can_translate)
            if can_translate:
                self.translate_btn.SetLabel(
                    _("Retranslate…") if has_tr else _("Translate…")
                )

        ver = kb_version_label()
        if ver:
            self.version_label.SetLabel(_("KB as of {date}").format(date=ver))
        else:
            self.version_label.SetLabel(_("KB not downloaded yet"))

        # Relayout after Show/Hide of choice / translate.
        self.Layout()

    def _page_to_show(self) -> Path | None:
        ref = self._ref
        if not ref:
            return None
        mode = None
        sel = self.lang_choice.GetSelection()
        if 0 <= sel < len(getattr(self, "_lang_modes", [])):
            mode = self._lang_modes[sel]
        if mode == "en" and ref.en_path:
            return ref.en_path
        if mode == "ja":
            ja = resolve_local_article(self._en_rel, ui_lang="ja", prefer_english=False)
            if ja and ja.preferred_path and ja.preferred_kind == "ja":
                return ja.preferred_path
            # Fall back to English if JA mirror missing.
            return ref.en_path
        if mode == "translation":
            if ref.translation_path and ref.translation_path.is_file():
                return ref.translation_path
            # Not translated yet — show English until Translate… runs.
            return ref.en_path
        if self._showing_english and ref.en_path:
            return ref.en_path
        return ref.preferred_path or ref.en_path

    def _show_html(self, html: str, *, base_url: str = "") -> None:
        self._last_html = html or ""
        display = html or ""
        if self._on_page_nav is not None and self._is_webview and display:
            lower = display.lower()
            idx = lower.rfind("</body>")
            if idx >= 0:
                display = display[:idx] + _KB_PAGE_NAV_SCRIPT + display[idx:]
            else:
                display = display + _KB_PAGE_NAV_SCRIPT
        if self._is_webview:
            try:
                # Empty base — document is self-contained (CSS inlined).
                self.view.SetPage(display, base_url or "")
            except Exception:
                try:
                    self.view.SetPage(display, "")
                except Exception:
                    pass
        else:
            try:
                self.view.SetValue(html)
            except Exception:
                pass
        self._notify_ready()

    def _navigate_to_path(self, path: Path) -> None:
        """Show a local article in the WebView (must run after the host is shown)."""
        if self._closing:
            return
        try:
            if not self:
                return
        except RuntimeError:
            return
        if not self._is_webview:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                self._last_html = text
                self.view.SetValue(text)
            except OSError:
                self.view.SetValue(_("Could not read the local article."))
                self._last_html = ""
            self._notify_ready()
            return
        try:
            # SetPage with inlined CSS — LoadURL(file://) often paints blank in
            # Edge WebView2 when the control was created before ShowModal.
            html = html_document_for_display(path)
        except OSError:
            self._show_html(
                _simple_message_html(_("Could not read the local article."))
            )
            return
        self._show_html(html)

    def _reload_content(self) -> None:
        if not self._view_realized:
            self._realize_view()
        self._refresh_ref()
        self._update_lang_choice()
        path = self._page_to_show()
        if path is None or not path.is_file():
            msg = _(
                "This Knowledge Base article is not available offline yet.\n"
                "Use Update… to download articles, or Go online to open it in your browser."
            )
            html = _simple_message_html(msg)
            self._show_html(html)
            return
        # One more defer so the freshly created WebView finishes realizing.
        wx.CallAfter(self._navigate_to_path, path)

    def _on_lang_choice(self, _event: wx.CommandEvent) -> None:
        global _prefer_english_session
        sel = self.lang_choice.GetSelection()
        if 0 <= sel < len(self._lang_modes):
            mode = self._lang_modes[sel]
            _prefer_english_session = mode == "en"
            self._showing_english = mode == "en"
            if mode == "translation":
                ref = self._ref
                if ref and (
                    not ref.translation_path or not ref.translation_path.is_file()
                ):
                    self._run_translation(notify_failure=True)
        self._reload_content()

    def _on_go_online(self, _event: wx.CommandEvent) -> None:
        url = self.online_url()
        try:
            webbrowser.open(url)
        except OSError:
            pass

    def _on_update(self, _event: wx.CommandEvent) -> None:
        run_kb_update_with_progress(self)
        self._reload_content()

    def _on_translate(self, _event: wx.CommandEvent | None) -> None:
        # Force a fresh translation (Retranslate…).
        lang = _ui_base_lang()
        if lang in ("en", "ja"):
            return
        if not _ensure_article_ui(self, self._en_rel):
            wx.MessageBox(
                _("Download the English article before translating."),
                _("DAISY Knowledge Base"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        tr = self._translate_with_progress(lang, force=True)
        if not tr.ok:
            self._show_translation_failure(tr)
            return
        self._showing_english = False
        self._reload_content()

    def _on_navigating(self, event) -> None:
        url = (event.GetURL() or "").strip()
        lower = url.lower()
        if lower.startswith("checkmate://page-prev"):
            event.Veto()
            if self._on_page_nav is not None:
                wx.CallAfter(self._on_page_nav, -1)
            return
        if lower.startswith("checkmate://page-next"):
            event.Veto()
            if self._on_page_nav is not None:
                wx.CallAfter(self._on_page_nav, 1)
            return
        if url.startswith(("http://", "https://")):
            event.Veto()
            from .store import is_kb_url

            if is_kb_url(url):
                wx.CallAfter(self._open_kb_link_in_panel, url)
            else:
                try:
                    webbrowser.open(url)
                except OSError:
                    pass
            return
        event.Skip()

    def _open_kb_link_in_panel(self, url: str) -> None:
        """Navigate another KB article in this panel (avoid nested modals)."""
        from .store import en_relative_path_from_url

        en_rel = en_relative_path_from_url(url)
        if not en_rel:
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        self.load_article(en_rel)


class KnowledgeBaseViewerDialog(wx.Dialog):
    """Modal dialog showing a local KB article with language toggle."""

    def __init__(
        self,
        parent: wx.Window | None,
        *,
        en_rel: str,
        ref: KbArticleRef | None = None,
    ) -> None:
        super().__init__(
            parent,
            title=_("DAISY Knowledge Base"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self._closing = False
        root = wx.BoxSizer(wx.VERTICAL)
        self.article = KnowledgeBaseArticlePanel(self, en_rel=en_rel, ref=ref)
        root.Add(self.article, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        btn_row = wx.StdDialogButtonSizer()
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("Close"))
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        btn_row.AddButton(close_btn)
        btn_row.Realize()
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 8)

        self.SetSizer(root)
        self.SetSize((900, 700))
        self.CentreOnParent()
        # Modal dialogs must EndModal — Close() alone can leave ShowModal hung.
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        close_btn.SetDefault()
        # Defer first load until the dialog HWND exists (Edge WebView2 paints
        # blank if LoadURL/SetPage runs during __init__).
        wx.CallAfter(self.article.start_initial_load)

    # Compatibility for open_knowledge_base_url(replace_dialog=…).
    @property
    def _en_rel(self) -> str:
        return self.article._en_rel

    @_en_rel.setter
    def _en_rel(self, value: str) -> None:
        self.article._en_rel = value

    @property
    def _showing_english(self) -> bool:
        return self.article._showing_english

    @_showing_english.setter
    def _showing_english(self, value: bool) -> None:
        self.article._showing_english = value

    def _reload_content(self) -> None:
        self.article._reload_content()

    def _on_close(self, _event: wx.Event | None = None) -> None:
        # Guard against SetEscapeId + Close button both firing.
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self.article.mark_closing()
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()


def run_kb_update_with_progress(parent: wx.Window | None) -> bool:
    """Run a full mapped-article update with a modal progress dialog. Returns success."""
    dlg = wx.ProgressDialog(
        _("Updating Knowledge Base"),
        _("Preparing Knowledge Base update…"),
        maximum=100,
        parent=parent,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_CAN_ABORT,
    )
    result: dict = {"summary": None, "error": None, "abort": False}

    def progress(msg: str) -> None:
        if result["abort"]:
            return
        wx.CallAfter(_pulse, dlg, msg, result)

    def worker() -> None:
        try:
            result["summary"] = update_knowledge_base(progress=progress)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while t.is_alive():
        wx.MilliSleep(50)
        wx.YieldIfNeeded()
        if dlg.WasCancelled():
            result["abort"] = True
    try:
        dlg.Destroy()
    except Exception:
        pass

    if result["error"]:
        wx.MessageBox(
            _("Knowledge Base update failed:\n{detail}").format(
                detail=result["error"]
            ),
            _("DAISY Knowledge Base"),
            wx.OK | wx.ICON_ERROR,
            parent,
        )
        return False
    summary = result.get("summary") or {}
    failed = summary.get("failed") or []
    date = (summary.get("commit_date") or "")[:10]
    if failed:
        detail = _(
            "Downloaded {ok} of {total} articles."
        ).format(ok=summary.get("ok", 0), total=summary.get("total", 0))
        if date:
            detail += "\n" + _("KB as of {date}.").format(date=date)
        detail += "\n" + _("{n} articles failed.").format(n=len(failed))
        wx.MessageBox(
            detail,
            _("DAISY Knowledge Base"),
            wx.OK | wx.ICON_WARNING,
            parent,
        )
    else:
        msg = _("Knowledge Base updated ({n} articles).").format(
            n=summary.get("ok", 0)
        )
        if date:
            msg += "\n" + _("KB as of {date}.").format(date=date)
        wx.MessageBox(
            msg,
            _("DAISY Knowledge Base"),
            wx.OK | wx.ICON_INFORMATION,
            parent,
        )
    return True


def _pulse(dlg: wx.ProgressDialog, msg: str, result: dict) -> None:
    try:
        cont, _skip = dlg.Pulse(msg)
        if not cont:
            result["abort"] = True
    except Exception:
        pass


def _ensure_article_ui(parent: wx.Window | None, en_rel: str) -> bool:
    """
    Silently ensure an article (and site CSS) are cached.

    Shows a short progress dialog only when a network download is needed.
    """
    css = None
    try:
        from ..paths import kb_dir

        css = kb_dir() / "site" / "css" / "kb.css"
    except Exception:
        pass
    already = en_file_path(en_rel).is_file() and (css is None or css.is_file())
    if already:
        # Refresh missing home assets if needed; usually a no-op.
        return ensure_article_cached(en_rel, also_ja=True)

    dlg = wx.ProgressDialog(
        _("Knowledge Base"),
        _("Downloading article…"),
        parent=parent,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
    )
    err: dict = {"ok": False}

    def worker() -> None:
        err["ok"] = ensure_article_cached(en_rel, also_ja=True)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while t.is_alive():
        wx.MilliSleep(40)
        try:
            dlg.Pulse(_("Downloading article…"))
        except Exception:
            pass
        wx.YieldIfNeeded()
    try:
        dlg.Destroy()
    except Exception:
        pass
    return bool(err["ok"])


def open_knowledge_base_url(
    parent: wx.Window | None,
    url: str,
    *,
    replace_dialog: KnowledgeBaseViewerDialog | KnowledgeBaseArticlePanel | None = None,
) -> bool:
    """
    Open a KB URL in the offline viewer when possible.

    Missing articles are downloaded silently on demand and cached for next time.
    Returns True if handled (viewer opened or fell back to the system browser).
    """
    from .store import en_relative_path_from_url, is_kb_url

    if not is_kb_url(url):
        return False
    en_rel = en_relative_path_from_url(url)
    if not en_rel:
        return False

    if not _ensure_article_ui(parent, en_rel):
        try:
            webbrowser.open(url)
        except OSError:
            pass
        return True

    prefer_en = _prefer_english_session if _ui_base_lang() != "en" else True
    ref = resolve_local_article(en_rel, ui_lang=get_language(), prefer_english=prefer_en)

    if replace_dialog is not None:
        try:
            panel: KnowledgeBaseArticlePanel | None = None
            if isinstance(replace_dialog, KnowledgeBaseArticlePanel):
                panel = replace_dialog
            elif isinstance(replace_dialog, KnowledgeBaseViewerDialog):
                panel = replace_dialog.article
            if panel is not None and panel:
                panel.load_article(en_rel, prefer_english=prefer_en)
                return True
        except RuntimeError:
            pass

    dlg = KnowledgeBaseViewerDialog(parent, en_rel=en_rel, ref=ref)
    try:
        dlg.ShowModal()
    finally:
        try:
            dlg.Destroy()
        except RuntimeError:
            pass
    return True


def open_knowledge_base_home(parent: wx.Window | None) -> None:
    """Help menu entry: open local KB home (downloads that page on demand)."""
    from .store import KB_HOME_URL

    open_knowledge_base_url(parent, KB_HOME_URL)
