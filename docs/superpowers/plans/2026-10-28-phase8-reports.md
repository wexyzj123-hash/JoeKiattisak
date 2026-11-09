# Phase 8 — Reports, Excel/PDF and Submission Deadlines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let safety staff view the monthly department report, annual summary, incident statistics and CAPA status from real data, export each to Excel and PDF (Thai), and track/remind report submission deadlines with an append-only "submitted" record.

**Architecture:** One pure data layer (`safety/reports.py`) turns the viewer's scope into a neutral `Report` structure (KPIs + tables). Two exporters (`safety/report_export.py`) render that structure to `.xlsx` (openpyxl) and `.pdf` (reportlab + bundled Thai TTF); the HTML page renders the same structure, so screen, Excel and PDF can never disagree. Deadlines live in `report_due` (admin-editable) and `report_submissions` (append-only, trigger-protected); reminders reuse the existing rule engine (`report_due_soon`, `report_overdue`) and the daily scheduler exactly as Phase 7 did for CAPA/patrol.

**Tech Stack:** Flask, SQLite, Jinja, openpyxl, reportlab, Pillow (existing), pytest, Playwright visual runner.

**Spec:** `docs/superpowers/specs/2026-10-28-safety40-phases-5-8-design.md` §5 (and §6 answers: lost-time days, injured names are restricted PII, Thai font "ทั่วไป").

## Global Constraints

- Secrets only from environment/.env; none in UI, logs or DB. No network access at runtime: the Thai font is bundled in the repo.
- Reports use the viewer's factory/department scope (`hazard_scope_sql`-style, `user_in_scope`); unscoped users see everything. Out-of-scope department ids → 404, never an empty 200.
- Injured-person names and other `incident_people` PII never appear in any report, page, xlsx or pdf. Owner/inspector names of staff are allowed.
- New permissions apply only to newly created roles via `DEFAULT_ROLES`; existing roles are granted at `/admin/roles` (same rule as every earlier phase). Admin always passes.
- Forms/POSTs use the global CSRF; append-only tables are protected by DB triggers; stale/duplicate submissions give a Thai flash message, never a 500.
- Thai UI text; all Excel cells starting with `= + - @` are written as text (formula-injection guard).
- New dependencies go in `requirements.txt` (`openpyxl>=3.1`, `reportlab>=4`).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_014EQNQmJHP9a3NYCiw9Fuh7`. Push only to `claude/vibrant-knuth-8b04r0`; no PR.

## Review Focus

- Viewer in factory A requests a department/report of factory B (or forges `department_id`) → 404, and counts/rows never include B's data.
- A period with zero data (new month, empty department, year with no incidents) → report still renders with zeros and "ไม่มีข้อมูล", xlsx/pdf still valid (no division by zero, no empty-sheet crash).
- Thai text with long strings, tone marks and combining vowels in PDF tables → wraps inside the cell, no tofu boxes, no overflow off the page; very large tables paginate.
- Free text starting with `=cmd|...`, `+`, `-`, `@` in titles/areas → stored as text in Excel; text with `<`/`&` is escaped in PDF paragraphs.
- Marking the same period submitted twice, concurrently, or for a future period → one record only / clear message; and `report_overdue` is not sent for a period already submitted; month-end/leap-day/year-boundary due dates (31 Jan→28 Feb, 29 Feb, December→January).

---

### Task 1: Dependencies and bundled Thai font

**Files:**
- Modify: `requirements.txt`
- Create: `shared/assets/fonts/` (font files + `LICENSE.txt`), `safety/report_fonts.py`
- Test: `tests/safety/test_report_fonts.py`

**Interfaces:**
- Produces: `report_fonts.register() -> tuple[str, str]` returns `(regular_name, bold_name)` registered with reportlab (idempotent); raises `FontMissing` (subclass of `RuntimeError`) with a Thai message if no usable TTF is found; `report_fonts.available() -> bool`.

- [ ] **Step 1: Decide and place the font.** Preferred: Sarabun Regular/Bold TTF (SIL OFL 1.1, from Google Fonts) copied to `shared/assets/fonts/Sarabun-Regular.ttf`, `Sarabun-Bold.ttf` with the OFL text in `LICENSE.txt`. This sandbox cannot download it (proxy 403), so fallback in this order: (a) files supplied by the user; (b) convert the system `Loma.otf`/`Loma-Bold.otf` (tlwg, GPL-2+ with Font exception — embedding in documents is permitted) to TrueType outlines with `fontTools` (`cu2qu`, one-off script kept in `tools/otf_to_ttf.py`, `fonttools` as a dev-only dependency) and bundle `Loma-Regular.ttf`/`Loma-Bold.ttf` with their license note. Record which one shipped in `LICENSE.txt` and README.
- [ ] **Step 2: Write failing tests** `test_register_returns_names_and_is_idempotent`, `test_font_covers_thai_glyphs` (reportlab `TTFont.face.charToGlyph` contains `ก`, `ำ`, `ิ`, `่`, `๑`), `test_missing_font_raises_font_missing_with_thai_message` (monkeypatch the font dir to an empty tmp dir).
- [ ] **Step 3: Run** `pytest tests/safety/test_report_fonts.py -v` → FAIL (module missing).
- [ ] **Step 4: Implement** `register()` searching `shared/assets/fonts` for `Sarabun-*.ttf` then `Loma-*.ttf`; register as `ReportFont`/`ReportFont-Bold` and `registerFontFamily`.
- [ ] **Step 5: Run tests** → PASS. **Commit** `feat(safety): bundled Thai font and report dependencies`.

### Task 2: Report data layer

**Files:**
- Create: `safety/reports.py`
- Test: `tests/safety/test_reports.py`

**Interfaces:**
- Consumes: `hazards.user_in_scope(db, user, area_id)`, `stats._visible`, `incident_stats` helpers, `capa.scope_sql(user)`, `timeutil.today()`.
- Produces dataclasses `Report(key, title, subtitle, period_label, generated_at, kpis: list[Kpi], tables: list[Table])`, `Kpi(label, value, note="")`, `Table(title, columns: list[str], rows: list[list], empty_text="ไม่มีข้อมูล")`; and
  - `REPORTS: dict[str, str]` key → Thai name (`monthly`, `annual`, `incidents`, `capa`)
  - `monthly_department(db, user, year: int, month: int, department_id: int | None) -> Report | None` (None = department outside scope)
  - `annual_summary(db, user, year: int) -> Report`
  - `incident_statistics(db, user, year: int) -> Report`
  - `capa_status(db, user, as_of: date | None = None) -> Report`
  - `build(db, user, key, year, month, department_id) -> Report | None`

Content (columns decided here): monthly = KPIs (hazards reported / closed / open at month end / overdue / high-risk open / incidents (accident, near-miss) / lost-time accidents / patrol rounds & average score / CAPA opened & verified / days since last lost-time accident) + tables by hazard type, by risk area, incidents (code, date, kind, severity, area — no people), open overdue hazards. Annual = 12-month table (hazards, closed, incidents, near-miss, lost-time) + totals + by department. Incidents = by month, kind, severity, area, cause, with injury-severity breakdown. CAPA = counts by status, overdue list (code, title, owner, due, days late), average days to verify, effectiveness checks pending.

- [ ] **Step 1: Write failing tests** with a small seeded fixture (two factories, hazards/incidents/CAPAs across months): `test_monthly_counts_match_seed`, `test_monthly_department_outside_scope_returns_none`, `test_scoped_user_numbers_exclude_other_factory`, `test_empty_period_returns_zero_kpis_and_empty_tables`, `test_incident_tables_never_contain_people_names` (seed `incident_people` with a recognisable name; assert it is absent from every cell and KPI), `test_annual_has_12_rows_and_totals_equal_sum`, `test_capa_status_overdue_list_and_average_days`, `test_year_and_month_validation` (month 0/13 → `ValueError`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the functions as SQL aggregates reusing the existing scope helpers; one query per table, no per-row queries. **Step 4: Run** → PASS. **Commit** `feat(safety): report data layer`.

### Task 3: Excel and PDF exporters

**Files:**
- Create: `safety/report_export.py`
- Test: `tests/safety/test_report_export.py`

**Interfaces:**
- Consumes: `Report`, `report_fonts.register()`.
- Produces: `to_xlsx(report) -> bytes` (sheet per table + first sheet "สรุป" with KPIs; frozen header row, column widths, Thai headers, text-guarded cells); `to_pdf(report) -> bytes` (A4, header with title/period/generated time, KPI grid, tables with wrapped cells via `Paragraph`, repeated header row, page numbers "หน้า x/y"); `safe_cell(value)` (prefix `'` for leading `= + - @`).

- [ ] **Step 1: Write failing tests:** `test_xlsx_roundtrip_values_and_sheets` (load with openpyxl), `test_xlsx_formula_injection_is_text` (value `=HYPERLINK(...)` → `cell.data_type == "s"`), `test_xlsx_sheet_names_valid_and_unique` (≤31 chars, no `[]:*?/\`), `test_pdf_is_valid_and_embeds_thai_font` (starts `%PDF`, contains `/FontFile2`), `test_pdf_escapes_markup_in_cells` (cell `<b>&</b>` does not raise), `test_pdf_paginates_300_rows` (page count > 1 via counting `/Type /Page`), `test_empty_report_exports_both`.
- [ ] **Step 2–4:** run FAIL → implement → PASS. **Commit** `feat(safety): excel and pdf report export`.

### Task 4: Permissions, routes, screens, navigation

**Files:**
- Modify: `safety/perms.py` (`report.view` "ดูรายงานและสถิติ", `report.export` "ส่งออกรายงาน Excel/PDF", `report.manage` "จัดการกำหนดส่งรายงาน"; `DEFAULT_ROLES`: assessor all three, dept_head view+export, viewer view), `safety/nav.py`, `safety/app.py` (register blueprint), `safety/search.py` only if needed (no)
- Create: `safety/views/reports.py`, `safety/templates/reports_index.html`, `safety/templates/report_view.html`
- Test: `tests/safety/test_report_views.py`, update `tests/safety/test_perms.py` and `tests/safety/test_nav.py` expectations.

**Interfaces:**
- Routes: `GET /reports` (cards per report + period picker), `GET /reports/<key>` (`year`, `month`, `department_id` query), `GET /reports/<key>.xlsx`, `GET /reports/<key>.pdf` (need `report.export`; `Content-Disposition: attachment; filename*=UTF-8''…`, ASCII-safe fallback name `report-<key>-<period>.xlsx|pdf`). Unknown key / out-of-scope department → 404; bad year/month → 400-style flash and default period. PDF route when no font → flash + redirect (Excel unaffected).
- Nav: group "รายงาน & สถิติ" with "รายงานและสถิติ" (`report.view`) and admin item "กำหนดส่งรายงาน" (`report.manage`, Task 5).

- [ ] **Step 1: Write failing tests:** `test_index_requires_report_view` (403 for reporter, 200 for dept_head), `test_view_renders_kpis_and_tables`, `test_export_needs_report_export_and_returns_valid_files` (xlsx opens, pdf `%PDF`), `test_out_of_scope_department_404`, `test_filename_header_is_ascii_safe`, `test_new_permissions_default_roles_only_new_roles`, `test_nav_shows_reports_for_view_holders_only`.
- [ ] **Step 2–4:** FAIL → implement (screens reuse `.card`, `.table-stack`, theme tokens, `charts.donut/trend` for the annual trend; mascot mood `thinking` on empty state) → PASS. **Commit** `feat(safety): report screens and exports`.

### Task 5: Deadlines and "mark as submitted"

**Files:**
- Modify: `safety/schema.py` (`REPORT_DDL`: `report_due(id, report_key CHECK IN ('monthly','annual','incidents','capa'), department_id NULL → whole company, frequency CHECK IN ('monthly','yearly'), due_day INTEGER 1–28, due_month INTEGER NULL (yearly only), active, UNIQUE(report_key, department_id, frequency) among active)`, `report_submissions(id, report_key, department_id, period TEXT 'YYYY-MM'|'YYYY', submitted_by, submitted_at, note)` + `UNIQUE(report_key, COALESCE(department_id,0), period)` + triggers blocking UPDATE/DELETE)
- Create: `safety/report_due.py`, `safety/views/report_due.py`, `safety/templates/report_due_admin.html`; add "ส่งรายงานแล้ว" form to `report_view.html`
- Test: `tests/safety/test_report_due.py`, `tests/safety/test_report_due_views.py`

**Interfaces:**
- Produces: `period_due(rule, today) -> tuple[str, date]` (period label and its due date; monthly rules refer to the previous month, yearly to the previous year; `add_months`-style clamping), `status(db, rule, today) -> {"state": "ok|due_soon|overdue|submitted", "period", "due", "days"}`, `mark_submitted(db, user, report_key, department_id, period, note) -> list[str]` (needs `report.export` or `report.manage`; viewer must have the department in scope; refuses future periods; duplicate → "ส่งรายงานงวดนี้แล้ว"; append-only), `rules(db, user) -> list` (scoped), `save_rule/toggle_rule(db, user, ...) -> list[str]` (`report.manage`, scoped like patrol plans — out-of-scope → "ไม่พบกำหนดส่ง").
- Admin screen `/admin/report-due`: list with state chips (ส่งแล้ว / ใกล้ครบ / เกินกำหนด), create/edit/deactivate, history of submissions.

- [ ] **Step 1: Write failing tests:** `test_period_due_month_end_and_year_boundary` (due_day 28 across Feb/leap, Dec→Jan, yearly), `test_mark_submitted_once_and_appends_event_only` (second call refused; direct `UPDATE`/`DELETE` raise `IntegrityError`), `test_mark_submitted_future_period_refused`, `test_submission_requires_scope_and_permission`, `test_rules_scoped_to_factory_idor` (update/toggle of another factory's rule → not found), `test_status_submitted_overrides_overdue`, `test_concurrent_double_submit_single_row` (two connections, one wins).
- [ ] **Step 2–4:** FAIL → implement (idempotent `ensure_schema`, tables created with `IF NOT EXISTS`) → PASS. **Commit** `feat(safety): report deadlines and submission record`.

### Task 6: Reminders through the rule engine and scheduler

**Files:**
- Modify: `safety/schema.py` (`NOTIFY_EVENTS` + `report_due_soon`, `report_overdue`; `REPORT_RULES` default rules seeded once under setting flag `report_rules_seeded`: due_soon −3 days → `role:dept_head` (in the department), overdue +1 → `role:dept_head`, overdue +3 → `role:assessor`; all `require_any: report.export|report.manage`), `safety/notify.py` (`emit_report(db, event, rule_id, actor_id, *, period, due, department_id, offset_days, days)` built on `_write_rows`; messages carry report name/period/department only), `safety/scheduler.py` (`_report_reminders(db, today)` using `_guarded`, skip submitted periods; `run_daily` returns `report_notifications`)
- Test: `tests/safety/test_report_notify.py`; update count/event-list assertions in `test_notify_schema.py`, `test_patrol_schema.py`, `test_scheduler.py` (default rule total 23 → 26).

**Interfaces:**
- Consumes: Task 5 `status`, `rules`; Phase 7 `_write_rows`, `_offsets`, `_guarded`.
- Dedupe key `report_<event>:<rule_row>:<period>:<offset>`, so a re-run the same day never duplicates and a new period reminds again.

- [ ] **Step 1: Write failing tests:** `test_rebuild_rules_adds_report_events_keeping_admin_edits`, `test_due_soon_and_overdue_emitted_once_with_catch_up`, `test_submitted_period_sends_nothing`, `test_reminder_goes_only_to_users_in_department_scope`, `test_message_has_no_free_text_or_names`, `test_run_daily_reports_error_isolation` (one broken rule does not stop others).
- [ ] **Step 2–4:** FAIL → implement → PASS; run the full suite. **Commit** `feat(safety): report due reminders`.

### Task 7: Polish, real-server check, docs, review

**Files:**
- Modify: `README.md` (Phase 8 section: reports, exports, font + license, deadlines, permissions to grant, upgrade note), `tests/visual/` fixtures/runner for `/reports`, `/reports/monthly`, `/admin/report-due`
- Create: scratchpad `walk8.py` (not committed)

- [ ] **Step 1:** Visual probes at 320/390/768/1280, light and dark: no horizontal scroll, contrast passes, tables stack on mobile, empty state shows mascot.
- [ ] **Step 2:** Real-server walkthrough: seed demo data, download the xlsx (open with openpyxl) and pdf (check `%PDF`, size, page count), submit a period, run the scheduler twice, confirm reminders appear once; upgrade an existing Phase 7 DB (no data loss, rules rebuilt, flag set once).
- [ ] **Step 3:** Full `pytest` green; commit and push.
- [ ] **Step 4:** One fresh whole-branch review (most capable model) of `git diff <phase 7 head>..HEAD`; verify each finding, fix tests-first, push, then report to the user.

## Self-review

- Spec coverage: report definitions with viewer scope (T2/T4), Excel (T3), PDF with bundled Thai font (T1/T3), `report_due` schedule (T5), reminders reusing rules/scheduler (T6), append-only "mark as submitted" (T5). PII rule enforced by `test_incident_tables_never_contain_people_names`.
- Open item for the user: the font file. If Sarabun cannot be placed by the user, the plan ships the converted Loma TTF (license noted) — please confirm which you prefer.
- Assumed (change if wrong): monthly report is due for the *previous* month on `due_day` of the next month; default reminder recipients are dept_head (department) then assessor.
