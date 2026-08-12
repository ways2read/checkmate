# CheckMate AI features — briefing

This note explains how CheckMate’s AI features work: credentials and model selection from FIDO, the connection check, then the main flows (overview, follow-up, explain, propose fix, apply and validate, alt-text inventory + health check).

Implementation lives under `checkmate/ai/` (LiteLLM client, session, overview, explain, fix, context, resources, alt_*) plus `checkmate/doc_images/` for EPUB/PDF image export. CheckMate does **not** import the FIDO package; it reads FIDO’s on-disk app-data files and vendors a slim document-image export stack.

---

## Tech stack (brief)

| Layer | Choice |
|--------|--------|
| App | Python ≥3.10, wxPython UI |
| LLM gateway | [LiteLLM](https://docs.litellm.ai/) (`checkmate/ai/litellm_client.py`) |
| Rendering | Markdown → HTML for dialogs |
| Providers | Whatever FIDO catalogs (Gemini, OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Groq, Gateway, LM Studio, Ollama, …) |

**Completion defaults** (temperature is left to the provider):

| Setting | Value |
|---------|--------|
| Completion timeout | 180s |
| Connection-check timeout | 30s |
| Explain / overview / fix `max_tokens` | 8192 |
| Follow-up `max_tokens` | 4096 |

Provider quirks are handled in `completion_output_kwargs()` (e.g. GPT-5 uses `max_completion_tokens`; some Gemini models disable thinking).

---

## Reading user prefs from FIDO

CheckMate reads JSON from FIDO’s app-data directory (`fido_settings.py`):

- Windows: `%LOCALAPPDATA%\fido\`
- macOS: `~/Library/Application Support/fido/`
- Linux: `~/.fido/`

| File | Role |
|------|------|
| `user_settings.json` | Model selection (`unified_llm_model`, optional `checkmate_model` / `describer_model`, `single_llm_for_all_services`) |
| `api_keys.json` | Provider API keys; optional `unlock_code` |
| `services.json` | Catalog: LiteLLM model id + which key field to use |

**Model selection order** (`selected_model_service_string()`):

1. If `single_llm_for_all_services` is true (FIDO default) → `unified_llm_model`
2. Else → `checkmate_model`, then `describer_model`
3. Else → in-memory unlock session model (if present)

Resolution (`resolve_litellm_model_and_key()`) splits `"Provider: model id"`, looks up the LiteLLM id and key name in `services.json`, and falls back to a small provider map when the catalog is missing. Gateway / LM Studio / Ollama also pick up `api_base` from FIDO keys.

**CheckMate-local settings** (separate from FIDO), e.g. `%LOCALAPPDATA%\CheckMate\settings.json`:

- `ai_features_enabled` — Tools → Settings… (Enable AI features)
- `ai_send_file_context` — whether file excerpts are included in explain/fix prompts (default on)
- `ai_send_kb_article_body` — Tools → Settings… (Include Knowledge Base article text in AI prompts; default off). See **Authoritative guidance** below: URL-only steering vs sending the article body (more tokens; often better answers because KB pages include current guidance and code samples).
- `show_issues_always` — Tools → Settings… (Show issues always; default off; opens the issues list automatically after a check that finds issues)
- `single_instance` — Tools → Settings… (Allow only one window; default on; a second launch focuses the existing window)
- `verapdf_flavour` — Tools → Settings… (`ua1` / `ua2`; default `ua2`)

AI features are offered when FIDO settings/keys are present (or unlock supplies credentials).

---

## Connection check

Before overview, explain, fix, or alt-text health check (not before follow-ups on an existing session):

1. Preload LiteLLM on a worker thread
2. Resolve model + API key (`ensure_credentials_ready()`); refresh unlock if needed
3. `ExplainSession.create()` → `check_provider_connection()`

The check is a minimal completion:

- Message: `"Hi"`
- `max_tokens`: 5
- Timeout: 30s

Failures are classified (`timeout`, `no_key`, `network`, `no_model`, `provider_error`, …) and mapped to localized UI strings. A successful check means later prompts reuse the same session credentials without repeating the probe.

---

## Shared runtime pattern

```text
UI action
  → resolve FIDO model/key
  → connection check ("Hi")
  → build system + user prompts (+ assets)
  → litellm.completion via ExplainSession
  → optional truncation continuation / fix repair
  → render markdown (or parse fix JSON)
```

`ExplainSession` holds the message history so follow-ups continue the same conversation.

### Cost logging (logger only)

After each LiteLLM completion (connection check, ask, follow-up/continuation), CheckMate logs estimated **USD cost** and token counts at INFO:

- Cost from `response._hidden_params["response_cost"]`, else `litellm.completion_cost(...)`
- Fields: `cost_usd`, `session_total_usd` (sum within the `ExplainSession`), `prompt_tokens`, `completion_tokens`, `total_tokens`
- Local / unpriced models log `cost_usd=n/a` — nothing is shown in the UI

---

## 1. AI overview

**Purpose:** Summarize an entire validation report for publishers/remediators.

**Entry:** `explain_overview()` in `ai/overview.py`.

### Assets / context

Built from the check result only (no file excerpts):

- Verdict, headline, fatal/error/warning/info/usage counts
- Tool name/version, publication kind, target name
- Per-checker counts and extra metadata
- Up to **50 unique** `(source, code)` issues, severity-sorted; messages trimmed (~180 chars)

### Prompt shape

**System** — accessibility publishing assistant; reply in the CheckMate UI language; exact H2 structure:

1. Overall assessment  
2. Main themes  
3. Suggested priorities  
4. Practical next steps  
5. Caveats  

Rules emphasize: use only the provided report data; don’t invent issues; group into themes; prioritize fatals/errors; keep sections concise markdown.

**User** — verdict, counts, publication/tool metadata, then the unique-issue list (or “no issues”).

### Params

- `max_tokens`: 8192  
- If the reply looks truncated (`finish_reason` length/max_tokens, or an unclosed ` ``` ` fence), one continuation is requested and merged.

---

## 2. Follow-up questions

Available after a successful **overview** or **issue explain**. The dialog keeps the `ExplainSession` and appends Q&A in the HTML view.

| Context | Function |
|---------|----------|
| Issue explain | `ask_followup()` in `ai/explain.py` |
| Overview | `ask_overview_followup()` in `ai/overview.py` |

### Behaviour

- Reuses full chat history (system + prior turns); **no** second connection check
- `max_tokens`: 4096
- Instructs the model to answer conversationally in the UI language
- Explicitly **not** to reuse the structured H2 layout from the original overview/explain
- Prefer short paragraphs or a few bullets; code only when helpful

---

## 3. AI explanation (single issue)

**Purpose:** Explain one checker message with practical remediation guidance.

**Entry:** `explain_issue()` in `ai/explain.py`.

### Assets

1. **Issue fields** — severity, source, code, location, message, tool, publication kind; for Ace also impact and ruleset (WCAG / EPUB / Best Practice labels from `rulesetTags`)  
2. **File excerpt** (if `ai_send_file_context` and EPUB/eBraille) — ~±20 lines around the hit, or OPF structural region / capped OPF; Ace locations resolved via CSS/HTML hints when needed  
3. **KB article body** (if `ai_send_kb_article_body` and a primary DAISY KB URL is known) — offline plain-text article (capped); downloaded on demand if not cached  
4. **Trusted resources** — curated URLs per checker (`ai/resources.py`):
   - **Ace:** specific DAISY KB article first (rule id map in `ai/ace_kb_map.py`, or Ace report help URL), then KB homepage / Ace docs
   - **EPUBCheck:** mapped DAISY KB article first when the code is accessibility-oriented (`ACC_*` and selected `HTM_*` / `NAV_*` in `ai/epubcheck_kb_map.py`); otherwise the official [EPUBCheck message reference](https://www.w3.org/publishing/epubcheck/docs/messages/) first; then EPUB a11y guidelines / KB homepage  
   - The model may only cite these under “Learn more”

### Authoritative guidance

When a **primary** reference is known (Ace KB article, EPUBCheck→KB map, or EPUBCheck message catalog):

- The Explain system prompt includes an **AUTHORITATIVE GUIDANCE** block naming that reference
- The model is told to align *What this means* / *Why it matters* / *How to fix* with it, and not invent conflicting requirements
- Learn more should list that reference first

Two modes for how much of that reference the model actually sees:

1. **Default (URL steering only)** — The prompt names the primary article and its URL. The model is steered to treat that page as authoritative, but it does **not** receive the article text. It must rely on training knowledge plus the checker message (and optional file excerpt). Cheaper; usually adequate when the model already “knows” the topic.
2. **Optional article body** (`ai_send_kb_article_body`) — The user prompt also includes a capped plain-text copy of the offline DAISY KB article (Ace help URL / rule map, or EPUBCheck codes mapped to a `kb.daisy.org` page — not the EPUBCheck message catalog). That supplies up-to-date KB wording and often concrete code samples the model would not otherwise have. More tokens; expected to produce better, more faithful remediation guidance. Off by default so the cost/quality trade-off can be evaluated.

### Prompt shape

**System** — same assistant role; UI language mandatory; exact H2s:

1. What this means  
2. Why it matters  
3. Where in the file  
4. How to fix  
5. Learn more  

Rules: authoritative guidance when available (else “don’t invent conformance requirements”); prefer concrete EPUB/eBraille steps; “Learn more” only as markdown links from the trusted list.

**User** — issue metadata + optional fenced file excerpt + optional fenced KB article body.

### Params

- `max_tokens`: 8192  
- Connection check first; one automatic continuation if truncated

---

## 4. Proposing an AI fix

**Purpose:** Propose a **minimal, unique text replacement** for one issue (EPUB / eBraille only).

**Entry:** `propose_fix()` in `ai/fix.py`.

### Flow

1. Credentials + connection check  
2. Gather issue context + member kind (`opf` / `html` / `css` / `other`)  
3. Ask with fix system/user prompts (`max_tokens` 8192)  
4. Parse markdown rationale + a single ` ```json ` fence → `FixProposal(file, original, replacement, rationale)`  
5. Validate: complete (not truncated/draft); `original` non-empty; occurs **exactly once** in the package member  
6. On failure: one repair follow-up (`_repair_user_prompt`), then re-parse

### Prompt shape

**System** — final answer only (no thinking aloud). Required output:

1. `## Proposed fix` — short rationale in the UI language  
2. Exactly one JSON object with string fields: `file`, `original`, `replacement`

Key rules: minimal edit; never empty `original` (insert-via-replace using a unique nearby anchor); prefer short snippets; may patch related OPF when that’s where the fix belongs; omit JSON if no safe automated fix.

**User** — issue metadata, optional **AUTHORITATIVE GUIDANCE** (primary Ace KB article, EPUBCheck→KB map, or EPUBCheck message catalog when known — prefer that remediation approach, but still copy patch text only from file excerpts), optional **KB article body** (when `ai_send_kb_article_body` is on and a DAISY KB article is primary), FILE TYPE guidance, cross-file guidance, **Exact file text** (raw excerpt for copying `original`), optional **Related package document** OPF excerpt.

The UI shows a preview (`format_fix_preview()`: Proposed fix / File / Before / After) before the user applies anything.

### Suggest fix for many (multi-patch)

**Purpose:** Same safety model (unique replaces only), but suggest **N patches** for every issue sharing the seed’s **source + code** in the current report.

**Entry:** Issue details → **Suggest fix for many** (enabled when matching count > 1) → `propose_batch_fix()`.

1. `gather_batch_fix_context()` — instance list + per-member Exact file text (caps: ~40 issues, ~12 members, ≤20 patches)  
2. Model returns `## Proposed fix` + JSON `{ "patches": [...], "skipped": [...] }`  
3. Each patch validated sequentially (exactly-once `original` after prior patches in that member)  
4. Preview via `format_batch_fix_preview()`; apply via `apply_proposed_fixes()` → `apply_text_replacements()` in **one** backup/rebuild cycle  
5. Changelog uses `log_batch_fix_applied()`; validation counts matching source+code before/after (`evaluate_fix_outcome(..., batch_mode=True)`)

Single-issue **Suggest fix with AI** is unchanged.

---

## 5. Executing a fix and validating the result

### Apply

1. User clicks **Apply fix and validate** → `apply_proposed_fix()` / `apply_proposed_fixes()` → `epub_package.apply_text_replacement(s)()`  
2. Exact once-replace (with CRLF-normalized fallback); multi-patch applies sequentially per member  
3. Write `.bak` backup(s), then:
   - Exploded folder: edit member(s) in place (extra member `.bak` files recorded for revert)  
   - Packaged `.epub` / `.ebrl`: extract → edit → rebuild  
4. Append an **edit changelog** entry (`edit_log.py`) beside the publication naming the backup file, issue, member(s), and excerpts  
5. On success: issue dialog closes with apply; main window stores `PendingFixVerify` and **re-checks** the publication

### Validate

`evaluate_fix_outcome()` compares the pre-apply `CheckResult` with the post re-check:

| Check | Pass when |
|--------|-----------|
| Target resolved | Single: same issue gone. Batch: matching source+code count is **0** |
| Counts reduced | fatals + errors + warnings decreased |
| Ace side effects | If the fixed issue was Ace: no **new** EPUBCheck fatal/error fingerprints |

`FixVerifyReport.has_concerns` is true if the target is still present, counts did not drop, or an Ace fix introduced new EPUBCheck errors.

**UI outcomes:**

- No concerns → “Fix confirmed” (logged as `confirmed`)  
- Concerns (and backup exists) → **Revert** / **Keep** buttons (`restore_from_backup` on Revert, including extra member backups for exploded multi-file batches); choice is logged  
- Re-check itself ends in error verdict → same **Revert** / **Keep** dialog (logged)  

**Changelog location:**

- Packaged file `book.epub` → `book.epub.checkmate-changelog.md` in the same folder  
- Exploded folder → `checkmate-changelog.md` inside the folder  

Confirm / revert dialogs show the changelog path when present. Open the log anytime via **Report → View edit changelog…** (enabled when a changelog exists for the current publication); it opens in a styled WebView.

---

## 6. Alt-text inventory report + AI health check

**Purpose:** After a check, browse all images and alt text for the publication, then optionally run an AI health check on decorative/content status and alt quality.

**Entry:** Result-row **Alt text** button (after **AI overview**), enabled when the checked path is a packaged `.epub` / `.ebrl` / `.pdf`. Click exports images via `checkmate.doc_images` and opens an in-app inventory WebView (`alt_text_report.html`). The HTML header shows **Exported by: CheckMate** (Fido exports use their own label; opening a cached Fido-branded report in CheckMate regenerates the HTML). The export is **cached** for that publication (path + mtime + size) so reopening Alt text does not re-extract until the file changes. From that dialog, **Run AI health check…** (when AI features are on) starts the vision sample flow after the inventory WebView has closed (deferred so Edge teardown does not freeze the next modal). Progress dialogs use the title **Alt text health check**, are cancellable (`PD_CAN_ABORT`), and status changes are spoken to screen readers.

### Inventory flow

1. Extract images + CSV (+ HTML) for the current publication  
2. Show interactive filter/search report in CheckMate (`AltTextReportDialog`, `LoadURL` so `images/` resolve)  
3. Optional: open in browser / open export folder  

### AI health-check flow (from inventory)

1. Load/validate the export (CSV column `Alt Text` with a space)
2. **Pass A** — local heuristics (no AI): missing/placeholder/filename alt, empty “Has Alt Text”, decorative+content-like classification mismatch, duplicates, very short alt
3. User chooses coverage when there is more than one option: **all** when ≤20 images (runs immediately — no picker); otherwise **10% / 25% / 50% / all** (samples are stratified through the publication by index). From the assessment report, **Assess more…** can raise coverage without redoing prior images (also skips the picker when only one choice remains).
4. Connection check; reject clearly non-vision models (`no_vision`)
5. **Pass B** — one vision call per sampled image (resized to FIDO `image_resize_pixels`, JPEG-compressed under FIDO `image_compression_mb`; LiteLLM multimodal `image_url` data URI). Omit OpenAI `detail` for Gemini models (LiteLLM would map it to a rejected `mediaResolution`). The prompt includes author alt/status, classification, Pass A flags, and **surrounding page text** from the export `Context` column when present — so quality is judged for document fit, not a generic caption style. Fatal BadRequest-style provider errors abort the sample early instead of failing every image.
6. Text-only **document synthesis** (fixed H2s) + write `alt_text_assessment.json` beside the export
7. HTML **Alt text health check** dialog (shared CheckMate AI webview styling: `aside.ai-note` disclaimer, doc header, stats, priority cards with **embedded data-URI thumbnails**/filters) + follow-up chat + Assess more

Export CSV columns: `Index`, `Filename`, `Classification`, `Alt Text`, `Status`, `Dimensions`, `File Size`, `Context` (optional surrounding text from `backend.get_context`). Older exports without `Context` still load; CheckMate’s export cache prefers folders that include the column so reopen after upgrade re-extracts once.

### Per-image JSON (vision)

Enums include `verdict`, `confidence`, `recommended_status`, quality axes, closed `issues` vocabulary. `suggested_alt` is always `null` in v1 (reserved for a later suggest/apply phase).

### Synthesis headings

1. Overall assessment  
2. Main themes  
3. Priority queue  
4. What good alt text means here  
5. Caveats  

### Params

- Vision `max_tokens`: 2048 (+ one JSON repair follow-up)  
- Synthesis `max_tokens`: 8192 (+ continuation if truncated)  
- Session: `ExplainSession.ask_multimodal()` for vision; synthesis/follow-up reuse text session  

### Phased later (not in v1)

- **v2 Suggest** — fill `suggested_alt` when confidence warrants  
- **v3 Apply** — write alts back into the publication with backup/changelog  

---

## Feature cheat sheet

| Feature | Connection check | `max_tokens` | Main assets |
|---------|------------------|--------------|-------------|
| Overview | Yes | 8192 (+ cont.) | Report summary, ≤50 unique issues |
| Explain | Yes | 8192 (+ cont.) | Issue + file excerpt + trusted URLs; Ace/EPUBCheck primary reference as authoritative topic |
| Follow-up | No (reuse session) | 4096 | Full chat history |
| Fix propose | Yes | 8192 (+ 1 repair) | Issue + raw excerpt + related OPF; optional Ace/EPUBCheck primary URL (approach only) |
| Fix apply / validate | N/A (local) | — | Unique string replace → re-check → confirm or revert |
| Alt-text inventory | N/A | — | Post-check **Alt text** button; `doc_images` export + in-app HTML |
| Alt-text AI health check | Yes | 2048/image + 8192 synth | From inventory dialog; Pass A heuristics; vision sample/all |

---

## Key source files

| Area | Path |
|------|------|
| LiteLLM + connection check | `checkmate/ai/litellm_client.py` |
| Chat session (incl. cost logging + multimodal ask) | `checkmate/ai/session.py` |
| Overview + overview follow-up | `checkmate/ai/overview.py` |
| Explain + explain follow-up | `checkmate/ai/explain.py` |
| Propose / apply / verify fix | `checkmate/ai/fix.py` |
| Alt-text export ingest + HTML ensure | `checkmate/ai/alt_export.py` |
| Alt-text doc export (EPUB/PDF) | `checkmate/ai/alt_build_export.py`, `checkmate/doc_images/` |
| Alt-text Pass A heuristics | `checkmate/ai/alt_heuristics.py` |
| Alt-text sampling | `checkmate/ai/alt_sample.py` |
| Alt-text vision assess + synthesis | `checkmate/ai/alt_assess.py` |
| Alt-text AI HTML report | `checkmate/ai/alt_report.py` |
| Alt-text inventory dialog | `checkmate/ai/alt_inventory_dialog.py` |
| Alt-text AI result dialog | `checkmate/ai/alt_dialog.py` |
| Issue + file context | `checkmate/ai/context.py` |
| Trusted “Learn more” links | `checkmate/ai/resources.py` |
| Ace rule → KB article map | `checkmate/ai/ace_kb_map.py` |
| EPUBCheck code → messages/KB map | `checkmate/ai/epubcheck_kb_map.py` |
| FIDO prefs / keys | `checkmate/fido_settings.py` |
| Package text replace / backup | `checkmate/epub_package.py` |
| Edit changelog (AI fix audit trail) | `checkmate/edit_log.py` |
