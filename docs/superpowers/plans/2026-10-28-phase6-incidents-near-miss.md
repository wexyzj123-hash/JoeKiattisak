# Safety 4.0 — Phase 6 (Incident / Accident + Near Miss) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an incident module beside the hazard register (a hazard is a *condition*, an incident is an *event*): accident and Near Miss reports, investigation with root cause and corrective action, four-eyes closing, notifications, statistics, and the "days without lost-time accident" KPI on the dashboard.

**Architecture:** New tables + a self-migrating schema block in `safety/schema.py`; new modules `safety/incidents.py` (rules, visibility, report), `safety/incident_flow.py` (state transitions), `safety/incident_stats.py`, views `safety/views/incidents.py` + `injury_admin.py`. It copies the hazard patterns exactly: optimistic `version` lock (`WHERE status=? AND version=?`), append-only event table with triggers, notifications written in the same transaction through the existing rule engine (generalised, not forked), photos through the existing image pipeline, role-based visibility + factory/department scope.

**Tech Stack:** Phase 1–5 stack; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-safety40-phases-5-8-design.md` §3 (module) and §6 (user answers). Hazard patterns to mirror: `safety/workflow.py`, `safety/hazards.py`, `safety/views/hazards.py`, `safety/notify.py`.

## Global Constraints

- **"วันปลอดอุบัติเหตุ" = days since the last *lost-time* accident** (status not `rejected`), counted in whole days from `occurred_at`'s date to today; over the viewer's factory/department scope (company-wide when unscoped); no accident on record → no number, text "ยังไม่มีอุบัติเหตุหยุดงานที่บันทึก". Never invented; shown on the dashboard only when computable or with that text.
- **Injured-person names are allowed but restricted PII:** `incident_people` rows are visible only to users with `incident.investigate` or `incident.close` (admin has all) and to that incident's own reporter. They never appear in notifications (in-app or external), search, lists, dashboard, statistics, event/audit text, logs, or the notification outbox. Everyone else sees only "ผู้เกี่ยวข้อง n คน". People text fields are plain text, escaped on output, length-capped (name 120, employee no. 30, note 300), max 20 people per incident.
- **Severity scale (general factory standard, admin-editable):** `injury_severities` seeded once: `property_only` ทรัพย์สินเสียหาย (ไม่บาดเจ็บ) rank 0 · `first_aid` ปฐมพยาบาล 1 · `medical` รักษาพยาบาล (ไม่หยุดงาน) 2 · `lost_time` หยุดงาน 3 (counts_lost_time=1) · `permanent` ทุพพลภาพถาวร 4 (1) · `fatality` เสียชีวิต 5 (1). Admin may rename, reorder, toggle `counts_lost_time`, deactivate; never delete; seeds never re-added once the seeded flag exists.
- Incident codes `IN-YYMM-####` (unique, retry once on collision like hazards). `kind` ∈ `accident|near_miss`. Statuses: `reported → investigating → actions_set → closed`, plus `rejected` from any open status. Dates stored `YYYY-MM-DD HH:MM` local; `occurred_at` may not be in the future and may not be older than 5 years.
- Permissions (new keys): `incident.report` (create + see own), `incident.investigate` (assign/record investigation, edit people, see all in scope, reject), `incident.close` (close). Default roles (only when the role row is first created): assessor = report+investigate+close, dept_head = report+investigate, reporter = report. Existing databases keep their role rows; admin grants as needed. "No one can assign/grant beyond own permissions" rule unchanged.
- **Four-eyes:** the user who closes must never have been the investigator (`owner_id`) of that incident at any time (derived from events). Closing needs `incident.close`; a rejected/closed incident accepts no further transition (stale submissions get the standard lost-race message).
- Notification events added (rule engine, admin-editable rows): `incident_reported`, `incident_assigned`, `incident_closed`. Default rules: reported → `role:assessor` (inapp,telegram); assigned → `owner` (all channels); closed → `reporter` (inapp). Lost-time-or-worse accidents are `urgent`. Message text contains code, kind, title, area — **no names**.
- Statuses, kinds and severities appear in Thai in the UI; all user text escaped; CSRF on every POST; unseen incident ⇒ 404 (not 403).
- Existing behavior must keep working: all 886 existing tests stay green except where a task says a test is updated (nav groups, notify admin event list, dashboard KPI count).

## Review Focus

- Names of injured people leaking outside the allowed audience: incident detail as plain reporter of *another* incident, list, search, dashboard, notification text, outbox rows, events text, JSON of any endpoint (Tasks 3, 5, 6).
- Time edge cases: `occurred_at` in the future, 29 Feb, timezone-less strings, accident exactly today (0 days), lost-time accident on a rejected record, severity later deactivated or its lost-time flag toggled (the KPI must follow the live flag; historic rows keep the severity id) (Tasks 1, 6).
- Concurrent transitions: two investigators assigning, close racing reject, double submit of close (exactly one event + one notification) (Task 4).
- Closing by the investigator via crafted POST; assigning an investigator outside scope or inactive or without `incident.investigate` (Task 4).
- Migration of an existing production database: `notify_rules` rebuilt without losing admin-edited rows or ids, idempotent on re-run, still rejects unknown events in Python (Task 2).
- Near Miss potential-risk scoring must reuse current criteria and snapshot level fields like hazards; scale sizes changed later must not break old rows (Task 3).

## File Structure

```
safety/schema.py        modify: INCIDENT_DDL, injury severity seed, notify_rules rebuild, new events, notifications.incident_id
safety/perms.py         modify: 3 permission keys
safety/images.py        modify: save_attachment generalised (table/fk whitelist)
safety/notify.py        modify: generic _emit + emit_incident + render_incident_message
safety/incidents.py     create: codes, visibility, report(), people
safety/incident_flow.py create: assign/record/close/reject
safety/incident_stats.py create: counts, trend, days_without_lost_time
safety/master.py        modify: REGISTRY += incident_causes
safety/nav.py           modify: group "อุบัติเหตุ" + admin items
safety/views/incidents.py, injury_admin.py   create
safety/views/home.py, notify_admin.py        modify (KPI card; Thai event names)
safety/templates/{incident_list,incident_form,incident_detail,incident_stats,injury_admin}.html, home.html   create/modify
tests/safety/{test_incident_schema,test_incident_notify,test_incidents,test_incident_flow,test_incident_views,test_incident_stats}.py   create
```

---

### Task 1: Schema, seeds, permissions, severity admin, cause master

**Files:** Modify `safety/schema.py`, `safety/perms.py`, `safety/master.py`, `safety/app.py` (register blueprint); Create `safety/views/injury_admin.py`, `safety/templates/injury_admin.html`; Test `tests/safety/test_incident_schema.py`

**Interfaces:**
- Produces tables: `injury_severities(id, code UNIQUE, name_th, rank, counts_lost_time, active)`; `incident_causes` via the existing master engine (`REGISTRY["incident-causes"]`, label "สาเหตุอุบัติเหตุ"); `incidents(id, code UNIQUE, kind CHECK, occurred_at, risk_area_id, title, description, immediate_action, injury_severity_id NULL, likelihood, severity, score, risk_level_id, level_name, level_color, level_requires_approval, reported_by, reported_at, status CHECK, owner_id, cause_id, root_cause, corrective_action, due_date, closing_note, reject_reason, lost_days, related_hazard_id, version, updated_at)`; `incident_people(id, incident_id, role CHECK injured|witness|involved, name, employee_no, note, created_at)`; `incident_events` (same columns as `hazard_events` with `incident_id`, same three append-only triggers); `incident_attachments(id, incident_id, kind CHECK before|after, stored_name, orig_name, size, uploaded_by, uploaded_at)`; `notifications.incident_id`.
- Produces `PERMISSIONS` keys `incident.report|incident.investigate|incident.close`; `INCIDENT_STATUS_TH`, `INCIDENT_KIND_TH` dicts in `safety/incidents.py` later (Task 3). Route `GET/POST /admin/injury-severities` (permission `criteria.manage`).

- [ ] **Step 1: Write failing tests:** `test_ensure_schema_creates_incident_tables_idempotently` (run twice, all tables present); `test_injury_severities_seeded_once_and_admin_changes_survive` (rename + deactivate one, call `ensure_schema` again → unchanged, count 6, flags `{lost_time, permanent, fatality}` = 1); `test_incident_events_append_only` (UPDATE/DELETE/REPLACE raise `IntegrityError`); `test_incident_check_constraints` (bad kind/status/people role rejected); `test_new_permission_keys_and_default_roles` (assessor has all three, dept_head report+investigate, reporter report, viewer none; admin gets all); `test_incident_causes_master_page_works` (`/master/incident-causes` 200 for admin, create row via existing master form); `test_injury_admin_requires_permission_and_saves` (403 for viewer; admin POST renames, toggles lost-time, reorders; no delete route; name 200 chars max; CSRF enforced); `test_existing_db_upgrade` (create a DB with only Phase-5 schema, run `ensure_schema`, incident tables appear and old data intact).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement `INCIDENT_DDL` appended to the `ensure_schema` script, `seed_injury_severities(con)` guarded by `app_settings` key `injury_severities_seeded`, `_add_column(con, "notifications", "incident_id", "INTEGER")`, the three permissions with Thai labels, role defaults appended in `DEFAULT_ROLES`, the master registry entry (+ `_master("incident_causes")` in `MASTER_DDL`, seed 5 default causes once: คน, เครื่องจักร/อุปกรณ์, วิธีการทำงาน, วัสดุ, สภาพแวดล้อม), the admin page (form per row using the `form=` attribute pattern, class `table-stack`). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): incident schema, injury severities, permissions`.

### Task 2: Generalise images and the notification engine

**Files:** Modify `safety/images.py`, `safety/schema.py` (NOTIFY_EVENTS + rule migration + default rules), `safety/notify.py`, `safety/views/notify_admin.py`; Test `tests/safety/test_incident_notify.py`

**Interfaces:**
- Produces `images.save_attachment(db, upload_root, owner_id, kind, filename, data, user_id, scope: str = "hazard") -> int` where `scope ∈ {"hazard","incident"}` selects `hazard_attachments/hazard_id` or `incident_attachments/incident_id` from a fixed whitelist (any other value raises `ValueError`); incident files live under `<upload_root>/incident-<id>/`. Existing call sites keep working unchanged.
- Produces `NOTIFY_EVENTS` extended with `incident_reported, incident_assigned, incident_closed`; `notify.emit_incident(db, event: str, incident_id: int, actor_id: int | None) -> int` (same contract as `emit`: SAVEPOINT, never throws, never commits, dedupe key `"{event}:i{incident_id}:{rule_id}:{stage}"` with stage = status/owner), `notify.render_incident_message(event, incident, extra) -> tuple[str, str]` (no names), link `/incidents/<id>`.
- Migration `rebuild_notify_rules_if_needed(con)`: when the stored `notify_rules` SQL lacks `'incident_reported'`, recreate the table without the `event CHECK`, copy rows with ids, drop/rename in one transaction; Python-side validation (`event in NOTIFY_EVENTS`) stays in `notify_admin`.

- [ ] **Step 1: Write failing tests:** `test_save_attachment_incident_scope_writes_row_and_folder` and `test_unknown_scope_raises`; `test_rules_table_rebuilt_keeping_ids_and_admin_edits` (edit a rule, disable one, delete one, run migration twice → ids/values identical, new events insertable); `test_default_incident_rules_seeded_once` (delete them; second `ensure_schema` does not re-add); `test_emit_incident_reported_notifies_assessors_in_scope_not_actor`; `test_emit_incident_dedupes_and_stage_for_assigned_changes_with_owner`; `test_incident_message_has_no_people_names` (incident with an injured person named "สมชาย ใจดี": title/body of every notification and outbox row exclude it); `test_lost_time_accident_is_urgent_in_outbox`; `test_failure_in_incident_emit_does_not_break_caller` (monkeypatch to raise → returns 0, caller transaction intact); `test_notify_admin_lists_new_events_in_thai`; existing hazard notification tests unchanged.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Refactor the body of `emit` into a private `_emit(db, event, record, link, key_prefix, stage, hazard_id, incident_id, urgent, title, body)` used by both entry points (DRY; hazard behavior byte-identical, proven by the existing tests). Add Thai names to `notify_admin.EVENT_TH`. **Step 4:** Run `pytest -q` → all PASS.
- [ ] **Step 5:** Commit `feat(safety): generic notification engine for incidents`.

### Task 3: Incident rules, visibility and reporting

**Files:** Create `safety/incidents.py`; Test `tests/safety/test_incidents.py`

**Interfaces:**
- Produces: `INCIDENT_STATUS_TH`, `INCIDENT_KIND_TH`, `OPEN_STATUSES = ("reported","investigating","actions_set")`; `can_view_all(perms) -> bool` (`incident.investigate` or `incident.close`); `visible_incident(db, user, incident_id) -> Row | None` (scope via `hazards.hazard_scope_sql`-equivalent on `risk_area_id`, else reporter/owner only); `can_see_people(db, user, incident) -> bool`; `next_code(db, today=None) -> str`; `report(db, user, form: Mapping) -> tuple[int | None, list[str]]` creating the row, people rows (form fields `people_name_N`, `people_no_N`, `people_role_N`, `people_note_N`, N=0..19), the `reported` event (data JSON holds counts only, never names) and calling `notify.emit_incident`; `people_for(db, user, incident) -> list[Row] | None` (None when not allowed).
- Validation in `report`: needs `incident.report`; kind valid; `occurred_at` parses (`YYYY-MM-DDTHH:MM` or `YYYY-MM-DD HH:MM`), not future, within 5 years; title ≤ 200; description ≤ 2000; immediate_action ≤ 1000; risk area active and in scope; accident requires an active `injury_severity_id`; near miss requires potential likelihood+severity within current scale (snapshot via `risk.calc_score`/`level_for_score` exactly like `workflow.assess`) and ignores any posted severity id; optional `related_hazard_id` must be visible to the user; people rows with blank name are skipped, caps enforced.

- [ ] **Step 1: Write failing tests:** `test_report_accident_creates_row_event_and_notification`; `test_report_near_miss_snapshots_level_like_hazards`; `test_accident_requires_active_severity_and_near_miss_ignores_it`; `test_occurred_at_future_too_old_malformed_and_29_feb_boundaries`; `test_validation_limits` (title 201, people 21, name 121, employee 31 → errors, nothing inserted, no event); `test_area_must_be_in_scope_and_active`; `test_related_hazard_must_be_visible`; `test_code_format_and_collision_retry`; `test_visibility_reporter_sees_only_own_investigator_sees_scope`; `test_unseen_incident_is_none_not_error`; `test_people_visible_only_to_investigator_closer_and_own_reporter` (other reporter and viewer get `None`); `test_event_and_notification_text_never_contain_names`; `test_scale_size_change_later_does_not_break_old_near_miss_rows`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement mirroring `workflow.report` (same error-message style, `add_event`-equivalent helper `incidents.add_event(db, incident_id, actor_id, kind, from_status, to_status, note="", data=None)` that only writes JSON of non-PII data). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): incident reporting, visibility and people privacy`.

### Task 4: Investigation flow

**Files:** Create `safety/incident_flow.py`; Test `tests/safety/test_incident_flow.py`

**Interfaces (all return `list[str]` errors, `[]` on success, use the shared `LOST_RACE`/`NOT_FOUND` messages from `safety/workflow.py`):**
- `assign_investigator(db, user, incident_id: int, owner_id, due_date: str, note: str = "") -> list[str]` — needs `incident.investigate`; allowed from `reported|investigating`; target must be active, hold `incident.investigate`, be in scope of the area, not beyond the actor's own permissions; `due_date` today..+365 days; sets status `investigating`; event `assigned`; notify `incident_assigned`.
- `record_investigation(db, user, incident_id, cause_id, root_cause: str, corrective_action: str, lost_days) -> list[str]` — only the current investigator or an admin; from `investigating` (or re-record while `actions_set`); `cause_id` active; texts 1..2000; `lost_days` optional int 0..3650 (required when the severity counts as lost-time); status `actions_set`; event `investigated`.
- `close(db, user, incident_id, note: str) -> list[str]` — `incident.close`; from `actions_set`; four-eyes: not any past/present investigator; note 1..1000; event `closed`; notify `incident_closed`.
- `reject(db, user, incident_id, reason: str) -> list[str]` — `incident.investigate`; any open status; reason 1..500; event `rejected`.
- `update_people(db, user, incident_id, form: Mapping) -> list[str]` — `incident.investigate`; open statuses only; replaces the list inside one transaction; writes event `people_updated` with `{"count": n}` only.

- [ ] **Step 1: Write failing tests:** one happy path through all steps asserting statuses, `version` increments, exactly one event and one notification per step; `test_close_by_investigator_refused_even_if_admin_never_was_not` (admin who was assigned cannot close; a different admin can); `test_assign_rejects_out_of_scope_inactive_or_unprivileged_target_and_past_or_far_due_date`; `test_grant_ceiling_cannot_assign_beyond_own_permission`; `test_record_only_by_current_investigator_or_admin`; `test_lost_time_requires_lost_days`; `test_double_submit_close_creates_one_event_and_one_notification` (second call returns the lost-race error); `test_two_connections_race_assign_versus_reject_exactly_one_wins` (threads with separate connections); `test_closed_and_rejected_are_terminal`; `test_update_people_audit_has_count_only`; `test_unseen_incident_returns_not_found`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with one `_transition` helper identical in shape to `workflow._transition`. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): incident investigation workflow with four-eyes close`.

### Task 5: Screens, navigation, photos

**Files:** Create `safety/views/incidents.py`, templates `incident_list.html`, `incident_form.html`, `incident_detail.html`; Modify `safety/nav.py`, `safety/app.py`; Test `tests/safety/test_incident_views.py`, `tests/safety/test_nav.py`

**Interfaces:**
- Routes: `GET /incidents` (filters `kind`, `status`, `q`, `mine`, `page`; 20 per page; list shows code, kind chip, title, area, severity or potential level chip, status, occurred_at, people **count** only); `GET/POST /incidents/new`; `GET /incidents/<id>`; `POST /incidents/<id>/assign|investigate|close|reject|people|photos`; `GET /incidents/<id>/photo/<stored>` (same whitelist regex + ownership check as hazard photos, `nosniff`, JPEG only). Nav group `อุบัติเหตุ`: "รายงานอุบัติเหตุ / Near Miss" `/incidents` (icon `alert`, perm `incident.report`), "สถิติอุบัติเหตุ" `/incidents/stats` (icon `overview`, perm `incident.investigate`); items "สาเหตุอุบัติเหตุ" `/master/incident-causes` (group ข้อมูลองค์กร… perm `master.view`) and "ระดับความรุนแรงการบาดเจ็บ" `/admin/injury-severities` (ผู้ดูแลระบบ, `criteria.manage`). Detail page shows people block only when `can_see_people`, else "ผู้เกี่ยวข้อง n คน"; action forms only for permitted next steps; mascot `thinking` decorative in empty states, `celebrate` flash on close.

- [ ] **Step 1: Write failing tests:** list/detail/new render for admin, assessor, reporter; `test_reporter_sees_only_own_incidents_and_404_for_others`; `test_people_names_absent_from_list_detail_for_other_reporter_and_present_for_investigator_and_own_reporter`; `test_new_form_toggles_accident_vs_near_miss_fields_server_side_validation_still_enforced`; `test_every_post_requires_csrf_and_permission`; `test_photo_upload_limit_5_and_serving_whitelist_and_nosniff`; `test_xss_in_all_user_fields_escaped` (title, description, action, people note, closing note); `test_filters_and_pagination_keep_params`; `test_nav_group_and_items_per_role` and updated expectations in `test_nav.py` (new group order after "ระบบข้อมูลหลัก"; admin hrefs list); stacked-table class and labels present; all new pages added to the visual test list (`tests/safety/test_theme_visual.py`: incident list/new/detail in states reported and closed, injury admin, stats placeholder) so 320/390/768/1280 scroll, contrast and label checks cover them.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement views reusing helpers from `views/hazards.py` by import (`_uploads`, `_precheck`, `_int`, `_like`) rather than copying. **Step 4:** Run → PASS (including the visual job).
- [ ] **Step 5:** Commit `feat(safety): incident screens and navigation`.

### Task 6: Statistics and the lost-time KPI

**Files:** Create `safety/incident_stats.py`, `safety/templates/incident_stats.html`; Modify `safety/views/incidents.py`, `safety/views/home.py`, `safety/templates/home.html`; Test `tests/safety/test_incident_stats.py`, `tests/safety/test_dashboard.py`

**Interfaces:**
- Produces: `days_without_lost_time(db, user, today: date | None = None) -> tuple[int | None, str | None]` (days, last date `YYYY-MM-DD`); `counts_by_kind_status(db, user) -> dict`; `monthly_incidents(db, user, months=12) -> list[tuple[str, int, int]]` (month, accidents, near misses, zero-filled); `by_severity(db, user) -> list[tuple[str, int]]` (accident rows only, rejected excluded, severity name from live table); `by_area(db, user, limit=8) -> list[tuple[str, int]]`; route `GET /incidents/stats` (permission `incident.investigate`) with the same SVG helpers as the dashboard (`charts.trend`-style bars, `charts.donut`) and a text table fallback. Dashboard gets a 5th KPI card "วันปลอดอุบัติเหตุ (หยุดงาน)" with `data-kpi="days-no-lti"` — number from `days_without_lost_time` (scope-only, **not** reporter-restricted, no PII), or the text when none; also a Near Miss count link for users with `incident.report`.

- [ ] **Step 1: Write failing tests:** `test_days_counts_from_last_lost_time_date_today_is_zero_29_feb_year_boundary`; `test_rejected_and_non_lost_time_accidents_ignored`; `test_live_flag_toggle_changes_kpi_but_old_rows_keep_severity_id`; `test_deactivated_severity_still_counts_historically`; `test_no_lost_time_accident_returns_none_and_dashboard_shows_text`; `test_scope_limits_days_to_factory_and_unscoped_is_company_wide`; `test_monthly_counts_zero_filled_and_scope`; `test_by_severity_and_by_area_exclude_near_miss_and_rejected`; `test_stats_page_requires_permission_and_has_no_names` (people with names exist in DB; page text excludes them); `test_dashboard_kpi_count_is_five_and_text_has_no_fake_number` (update the earlier "4 KPI" expectations); constant query count with 150 incidents.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): incident statistics and days-without-lost-time KPI`.

### Task 7: Whole-feature verification, docs

**Files:** Modify `README.md`; adjust whatever the review/screenshot pass demands

- [ ] **Step 1:** Real-server walkthrough (Waitress, real SQLite) as admin, assessor, dept_head and plain reporter: report accident (with people, photos) → assign → investigate → close by a different user → check notifications/outbox rows contain no names, the KPI, stats page; repeat for a Near Miss; try crafted POSTs (close as investigator, out-of-scope assign, other reporter's incident id) and confirm 404/refusals.
- [ ] **Step 2:** Screenshots at 390 and 1280 px, both themes, for list/new/detail (accident closed, near miss reported)/stats/injury admin/dashboard KPI; view them and fix concrete defects.
- [ ] **Step 3:** Upgrade check: copy a Phase-5 database, start the app, confirm tables/rules appear and existing hazards/notifications are untouched.
- [ ] **Step 4:** README section "อุบัติเหตุและ Near Miss (เฟส 6)" (flow, permissions, PII rule, KPI definition, how to adjust severities). `pytest -q` all green. Commit `feat: Phase 6 docs and polish`; then one fresh whole-branch review and a fix pass.

---

## Self-Review

- **Spec coverage (§3, §6):** incidents table with kind/severity/people/root cause/related hazard/photos/events/version → Tasks 1, 3, 4; admin masters (causes, severities) → 1; statistics + KPI → 6; permissions → 1; notifications (`incident_reported/assigned/closed`) → 2, 3, 4; user answers (lost-time KPI, PII allowed but restricted, general severity scale) → Global Constraints + Tasks 1, 3, 6. Overdue reminders for corrective actions are deliberately left to Phase 7 (CAPA) per the spec's scope split — only the `due_date` field exists now.
- **Type consistency:** `save_attachment(..., scope)`, `emit_incident`, `report`, `assign_investigator`, `record_investigation`, `close`, `reject`, `update_people`, `can_see_people`, `days_without_lost_time` are named identically across tasks and tests.
- **Review Focus** lines each map to named tests (people leakage → T3/T5/T6, time edges → T3/T6, races → T4, four-eyes → T4, migration → T2, near-miss snapshot → T3).
- **Proportion:** the plan states decisions, interfaces and test names; bodies are left to the implementer.
