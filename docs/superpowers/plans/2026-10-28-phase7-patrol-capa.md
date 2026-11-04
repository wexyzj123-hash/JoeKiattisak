# Safety 4.0 — Phase 7 (Safety Patrol + CAPA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-editable inspection checklists bound to risk areas with a schedule, mobile-friendly patrol rounds that become immutable once submitted, and a generic CAPA (corrective/preventive action) object with owner, due date, four-eyes verification, effectiveness check and overdue escalation — sourced from hazards, incidents, patrol findings or created manually; a failed patrol item becomes a hazard or a CAPA in one click.

**Architecture:** Same patterns as Phases 3–6: self-migrating tables in `safety/schema.py`; pure modules `safety/patrol.py`, `safety/capa.py`, `safety/patrol_admin.py`; thin views; append-only event tables with triggers; optimistic `version` lock (`WHERE status=? AND version=?`); notifications through the existing rule engine (`notify._write_rows`, new `emit_capa` / `emit_patrol`); the daily scheduler gains CAPA and patrol-plan reminders with the same "catch up every reached level, dedupe by key" behaviour; photos through the existing image pipeline (`scope="patrol"`).

**Tech Stack:** Phase 1–6 stack; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-safety40-phases-5-8-design.md` §4. Patterns to mirror: `safety/incidents.py`, `safety/incident_flow.py`, `safety/scheduler.py`, `safety/notify.py`.

## Global Constraints

- **Assumptions (no user answer exists; flagged for the review):** schedule frequencies are `daily | weekly | monthly`; scoring is optional per item (`weight` 1–10, default 1) and a round's score is `passed_weight / (passed_weight + failed_weight)` rounded to a whole percent (N/A excluded; no scored items → no score); the "closing needs verified CAPAs" gate is a setting that **defaults OFF** (`app_settings.require_capa_verified`), so existing workflows do not change until an admin turns it on.
- **Checklists:** `code` unique (case-insensitive), items grouped by free-text `section` (≤ 100), item text ≤ 300, `require_photo_on_fail` flag, `weight` 1–10, ordering by `sort`. Items and checklists are never deleted — only deactivated. **A round snapshots section/text/weight/photo-flag into its answers when it starts**, so later checklist edits never change a round.
- **Plans:** one active plan per (checklist, risk area); `frequency` daily|weekly|monthly; optional `inspector_id` (an active user holding `patrol.perform` and in scope of the area). Next due = last *submitted* round date + 1 day / 7 days / 1 calendar month (day clamped to month end; 29 Feb → 28 Feb in non-leap years); a plan never inspected is due from its creation date. A plan is *due* on/after its due date and *overdue* after it; missing several periods produces **one** due state (no backfill rounds, no notification flood).
- **Rounds:** code `PT-YYMM-####`; status `draft → submitted`. Only the inspector (or admin) edits a draft. Submit requires every active item answered `pass|fail|na`, and **every `fail` with `require_photo_on_fail` has ≥ 1 photo attached to that answer**; fail notes ≤ 500. After submit the round and its answers are **immutable at the database level** (triggers abort UPDATE/DELETE on the round and its answers once `status='submitted'`); a draft may be deleted only by its inspector or an admin and only while it has no hazard/CAPA links. Photos: ≤ 5 per answer.
- **Findings:** each failed answer of a submitted round can create at most one hazard (`hazards.patrol_answer_id` unique when not null) and at most one CAPA (`capas.source_kind='patrol'`, unique `source_id`); creation is idempotent (double-click makes one).
- **CAPA:** code `CP-YYMM-####`; `source_kind ∈ hazard|incident|patrol|manual`; `action_type ∈ corrective|preventive`; statuses `open → in_progress → done → verified`, plus `cancelled`; owner (active, in scope, may be anyone with `capa.create`/`capa.verify`/`patrol.perform`/`incident.investigate`/`hazard.assign` or a plain user — owner needs no permission to work their own CAPA); due date today..+730 days; title ≤ 200, texts ≤ 2000. **Four-eyes:** verifier needs `capa.verify` and must never have been owner (current or past, from events) and must not be the one who marked it done. Verification may be rejected back to `in_progress` with a reason (counted, shown). Effectiveness: when verifying, an optional `effectiveness_due` date (≤ 730 days); afterwards a verifier records `effective|ineffective` + note once.
- **Gate:** when `require_capa_verified = '1'`, closing a hazard (`workflow.close`) or an incident (`incident_flow.close`) is refused while any non-cancelled CAPA of that source is not `verified`; the refusal names how many remain. Cancelled CAPAs never block.
- **Permissions (new keys):** `patrol.manage` (checklists, plans), `patrol.perform` (open/answer/submit rounds, create findings), `patrol.view` (see all rounds in scope), `capa.create`, `capa.verify`. Default roles (only when a role row is first created): assessor = `patrol.perform, patrol.view, capa.create, capa.verify`; dept_head = `patrol.view, capa.create`; admin = all; others none. Existing role rows are not modified (admin grants).
- **Visibility:** rounds and CAPAs follow factory/department scope; without `patrol.view`/`capa.create`/`capa.verify` users see only their own rounds (inspector) / CAPAs they own or created; anything else is 404.
- **Notifications (rule engine, admin-editable):** new events `capa_assigned`, `capa_done`, `capa_verified`, `capa_reopened`, `capa_due_soon`, `capa_overdue`, `patrol_due`, `patrol_overdue`. Default rules: assigned → `owner` (all channels); done → `role:assessor` (inapp); verified/reopened → `owner` (inapp); due_soon `owner` offset 2 (inapp,telegram); overdue offsets 1 `owner`, 3 `owner,role:dept_head`, 7 `role:admin,role:dept_head` (L1–L3 like hazards); patrol_due → `owner` (the plan's inspector; if none, `role:assessor` via a second rule) inapp offset 0; patrol_overdue offsets 1 and 5 → `owner,role:dept_head`. Only people who can open the target are notified (same `require_any` guard as incidents). Texts carry code/title/area only.
- Thai UI; all user text escaped; CSRF on every POST; unseen object ⇒ 404; every new page joins the visual test list (320/390/768/1280, both themes, stacked tables, contrast).
- All 995 existing tests stay green except where a task says a test is updated (nav groups, notify rule counts/event list, dashboard KPI count).

## Review Focus

- A submitted round changed through any path: crafted POST, reopened form, direct SQL `UPDATE`/`DELETE` on rounds/answers/their photos, two submits racing (exactly one wins, one set of counts) (Tasks 1, 4).
- Plan due calculation: month ends (31 Jan → 28/29 Feb), leap day, daily plans after a weekend, inspector missing many periods, the day a round is submitted late; reminders deduped per (plan, due date) and escalation levels caught up once (Tasks 2, 4).
- CAPA four-eyes by reassignment history, admin marking done then verifying, verifying one's own CAPA via a second role, verify after cancel, reject-back loop counts (Task 5).
- Closing gate: CAPA created or cancelled between the check and the close (race), gate off by default, source with only cancelled CAPAs, hazard closed via the existing UI still celebrates and notifies exactly once (Task 6).
- Findings idempotence and privacy: double-click/two tabs create one hazard/CAPA; a finding from another factory's round is a 404; photo required on fail cannot be satisfied with another answer's/round's photo (Tasks 4, 6).
- Checklist edits after a round started (rename, deactivate, reorder, delete attempt) must not alter or break draft and submitted rounds (Tasks 3, 4).

## File Structure

```
safety/schema.py        modify: PATROL_DDL, CAPA_DDL, triggers, hazards.patrol_answer_id, new events + default rules + settings seed
safety/perms.py         modify: 5 permission keys; schema.py default roles
safety/images.py        modify: SCOPES += patrol (patrol_attachments/answer_id)
safety/notify.py        modify: emit_capa, emit_patrol, render messages
safety/scheduler.py     modify: CAPA + patrol-plan reminders in run_daily
safety/patrol_admin.py  create: checklist/item/plan CRUD rules
safety/patrol.py        create: due calc, start/answer/submit, visibility, findings
safety/capa.py          create: create/start/complete/verify/reopen/cancel/reassign/effectiveness, gate helper
safety/workflow.py, incident_flow.py   modify: gate hook in close()
safety/views/{patrol_admin,patrols,capas}.py   create; safety/views/home.py modify
safety/templates/{checklists,patrol_plans,patrol_list,patrol_round,capa_list,capa_form,capa_detail}.html; home.html, layout nav
tests/safety/{test_patrol_schema,test_patrol_admin,test_patrol,test_capa,test_capa_gate,test_patrol_notify,test_patrol_views}.py
```

---

### Task 1: Schema, permissions, seeds

**Files:** Modify `safety/schema.py`, `safety/perms.py`, `safety/app.py`; Test `tests/safety/test_patrol_schema.py`, `tests/safety/test_schema.py`, `tests/safety/test_notify_schema.py`

**Interfaces — tables produced:** `checklists(id, code UNIQUE NOCASE, name_th, description, active, created_by, created_at)`; `checklist_items(id, checklist_id, section, text_th, require_photo_on_fail, weight, sort, active)`; `patrol_plans(id, checklist_id, risk_area_id, frequency CHECK, inspector_id NULL, active, created_by, created_at, UNIQUE partial (checklist_id, risk_area_id) WHERE active=1)`; `patrol_rounds(id, code UNIQUE, plan_id NULL, checklist_id, risk_area_id, inspector_id, status CHECK draft|submitted, started_at, submitted_at NULL, passed, failed, na, score_pct NULL, version)`; `patrol_answers(id, round_id, item_id, section, text_th, weight, require_photo, result CHECK pass|fail|na NULL, note, UNIQUE(round_id,item_id))`; `patrol_attachments(id, answer_id, stored_name, orig_name, size, uploaded_by, uploaded_at)`; `capas` and `capa_events` (append-only triggers as `hazard_events`) per Global Constraints plus `risk_area_id NOT NULL`, `created_by`, `done_by`, `done_at`, `verified_by`, `verified_at`, `effectiveness_due`, `effectiveness_result`, `effectiveness_note`, `reopen_count`, `patrol_answer_id`-style link via `source_kind/source_id`; `hazards.patrol_answer_id` (guarded `_add_column`) + unique partial index; `app_settings.require_capa_verified` seeded `'0'` once. Triggers `patrol_rounds_frozen`, `patrol_answers_frozen`, `patrol_attachments_frozen` abort UPDATE/DELETE when the parent round is `submitted` (INSERT of attachments/answers too).
- Permissions: the five keys; `DEFAULT_ROLES` extended as in Global Constraints.

- [ ] **Step 1: Write failing tests:** schema created and idempotent; seeds once (setting + the default rules for the 8 events: count and recipients per Global Constraints; deleted rules not re-added; rule table accepts all 8 events after the Phase 6 rebuild helper runs on an old DB); CHECK constraints (frequency, result, status, source_kind, action_type); unique active plan per (checklist, area) but a deactivated one may coexist; unique `hazards.patrol_answer_id`; **immutability**: after `UPDATE patrol_rounds SET status='submitted'` an UPDATE/DELETE on that round, its answers and their attachments raises `IntegrityError`, an INSERT of a new answer/attachment for it raises, while a draft round stays editable; `capa_events` append-only (update/delete/replace raise); new permission keys + default role grants and viewer none; upgrade test from a Phase-6 database (tables appear, old data intact).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement DDL/seed/permissions; update the existing tests that count rules or list permissions. **Step 4:** Run `pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat(safety): patrol and CAPA schema, permissions, seeds`.

### Task 2: Notifications, photos and scheduler for CAPA and patrol

**Files:** Modify `safety/notify.py`, `safety/images.py`, `safety/scheduler.py`; Test `tests/safety/test_patrol_notify.py`

**Interfaces:**
- `images.SCOPES["patrol"] = ("patrol_attachments", "answer_id", "patrol-")`; `save_attachment(..., scope="patrol")` (limit 5 per answer, ignores `kind`: a fixed `"before"` is passed by callers).
- `notify.emit_capa(db, event: str, capa_id: int, actor_id: int | None, *, offset_days: int | None = None, days: int | None = None) -> int` and `notify.emit_patrol(db, event: str, plan_id: int, actor_id: int | None, *, offset_days: int | None = None, days: int | None = None, due: str = "") -> int` — same contract as `emit_incident` (SAVEPOINT, never throws/commits, `require_any` guard: CAPA events need any of `capa.create|capa.verify` or being owner/creator; patrol events need `patrol.perform|patrol.view`); dedupe stage for CAPA due/overdue = offset; for patrol = `"<due date>:<offset>"`; link `/capas/<id>` and `/patrols?plan=<id>`; recipients: for CAPA `owner` = `owner_id`, `reporter` = `created_by`; for patrol plans `owner` = `inspector_id`.
- `render_capa_message(event, capa, extra)` / `render_patrol_message(event, plan, extra)` → code/title/area/due only.
- `scheduler.run_daily` additionally walks open CAPAs (`open|in_progress` with a due date) and active patrol plans computing `due_state` (Task 4 `patrol.plan_status`) using the same catch-up loop as hazards; per-item SAVEPOINT; one failing item never fails the run. Result dict gains `capa_notifications`, `patrol_notifications`.

- [ ] **Step 1: Write failing tests:** `test_emit_capa_assigned_notifies_owner_only_when_they_can_open_it`; `test_capa_due_soon_and_overdue_levels_catch_up_once_and_dedupe_across_runs` (3 simulated days incl. skipped days); `test_capa_done_notifies_assessors_in_scope_not_actor`; `test_patrol_plan_due_and_overdue_notify_inspector_or_fallback_role`; `test_patrol_reminder_deduped_per_due_date_and_new_period_notifies_again`; `test_messages_contain_no_free_text_notes` (done_note/verify_note/answer notes never in bodies); `test_scheduler_failure_in_one_capa_does_not_stop_the_run` (monkeypatch one to raise); `test_save_attachment_patrol_scope_limit_5_per_answer`; existing hazard reminder tests unchanged.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement reusing `_write_rows` and `require_any`. **Step 4:** Run `pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat(safety): CAPA and patrol notifications and daily reminders`.

### Task 3: Checklist and plan administration

**Files:** Create `safety/patrol_admin.py`, `safety/views/patrol_admin.py`, templates `checklists.html`, `patrol_plans.html`; Modify `safety/app.py`; Test `tests/safety/test_patrol_admin.py`

**Interfaces:** `create_checklist(db, user, form) -> tuple[int | None, list[str]]`, `update_checklist(db, user, checklist_id, form) -> list[str]`, `toggle_checklist(...)`, `add_item(db, user, checklist_id, form) -> tuple[int | None, list[str]]`, `update_item(...)`, `toggle_item(...)`, `create_plan(db, user, form) -> tuple[int | None, list[str]]`, `update_plan(...)`, `toggle_plan(...)` (all need `patrol.manage`; return Thai error lists; no delete anywhere). Routes `GET/POST /admin/checklists` and `/admin/checklists/<id>` (items), `GET/POST /admin/patrol-plans`.

- [ ] **Step 1: Write failing tests:** create/rename/deactivate checklist; duplicate code (case-insensitive) refused; item limits (section 100, text 300, weight 1–10, sort int); item edits keep the same id; toggling items never deletes; no route accepts deletion; plan rules (active area/checklist required, frequency valid, duplicate active plan refused, inspector must be active, hold `patrol.perform`, in scope); editing checklist **after** a round started leaves that round's answer snapshot untouched (round + answers read back identical, deactivated item still shown in the round); permission 403 for others; CSRF; XSS escaped; stacked tables with labels; admin pages in the visual list.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): checklist and patrol plan administration`.

### Task 4: Patrol rounds core

**Files:** Create `safety/patrol.py`; Test `tests/safety/test_patrol.py`

**Interfaces:**
- `plan_status(db, plan, today: date | None = None) -> dict` → `{"due": date, "state": "ok"|"due"|"overdue", "days": int, "last": date | None}` (rules in Global Constraints); `due_plans(db, user, today=None) -> list[dict]` (visible plans that are due/overdue, most overdue first).
- `start_round(db, user, plan_id=None, checklist_id=None, risk_area_id=None) -> tuple[int | None, list[str]]` (needs `patrol.perform`; area in scope; checklist active with ≥ 1 active item; copies active items into `patrol_answers` snapshot; one open draft per (inspector, checklist, area) — starting again returns the existing draft).
- `save_answers(db, user, round_id, form: Mapping, expected_version=None) -> list[str]` (fields `result_<answer_id>`, `note_<answer_id>`; draft only; only inspector/admin; unknown/other-round ids ignored with an error; notes ≤ 500); `add_photo(db, user, round_id, answer_id, filename, data, upload_root) -> list[str]` (answer must belong to that draft round).
- `submit_round(db, user, round_id, expected_version=None) -> list[str]` (validation per Global Constraints; one transaction: counts, `score_pct`, `status='submitted'`, `submitted_at`, version+1; losing racer gets the lost-race message).
- `visible_round(db, user, round_id)`; `rounds_for(db, user, limit, ...)`; `latest_rounds(db, user, limit=5) -> list[Row]` (submitted only; code, area, inspector name, submitted_at, passed/failed/na, score_pct).

- [ ] **Step 1: Write failing tests:** `plan_status` table-driven (daily/weekly/monthly × never-inspected, on due date, one day late, many periods late, 31 Jan monthly → 28 Feb and 29 Feb leap, year boundary); `test_start_snapshots_active_items_in_order_and_reuses_open_draft`; `test_start_refuses_out_of_scope_inactive_checklist_or_empty_checklist_or_no_permission`; `test_save_answers_validates_ids_results_and_lengths`; `test_submit_requires_all_items_answered`; `test_submit_requires_photo_on_required_fail_and_photo_must_belong_to_that_answer`; `test_counts_and_weighted_score_na_excluded_no_scored_items_is_none`; `test_submit_twice_race_two_connections_exactly_one_wins`; `test_submitted_round_rejects_save_photo_resubmit_and_direct_sql`; `test_only_inspector_or_admin_edits_draft_others_404`; `test_visibility_scope_and_view_all`; `test_latest_rounds_only_submitted_and_in_scope`; `test_checklist_edit_during_draft_does_not_change_snapshot`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): patrol rounds with immutable submissions`.

### Task 5: CAPA core

**Files:** Create `safety/capa.py`; Test `tests/safety/test_capa.py`

**Interfaces (all return `list[str]` errors unless stated; shared `LOST_RACE`/`NOT_FOUND`/`STALE_FORM` messages; each transition = status + event + notification in one transaction):**
- `create(db, user, form: Mapping, source: tuple[str, int] | None = None) -> tuple[int | None, list[str]]` (needs `capa.create`; for `source` kinds hazard/incident/patrol the area is taken from the source and must be visible to the user, `manual` requires `risk_area_id`; notifies `capa_assigned`).
- `start(db, user, capa_id)` (owner; open→in_progress), `complete(db, user, capa_id, note: str)` (owner or `capa.create` holder; open|in_progress→done; stores `done_by/done_at`; notifies `capa_done`), `verify(db, user, capa_id, note: str, effectiveness_due: str = "")` (four-eyes per Global Constraints; done→verified; notifies `capa_verified`), `reopen(db, user, capa_id, reason: str)` (verifier rights; done→in_progress; `reopen_count+1`; notifies `capa_reopened`), `cancel(db, user, capa_id, reason: str)` (creator or `capa.create`; open|in_progress; never verified/cancelled), `reassign(db, user, capa_id, owner_id, due_date)` (`capa.create`; open|in_progress; notifies `capa_assigned`), `record_effectiveness(db, user, capa_id, result: str, note: str)` (verifier rights, verified only, once).
- `visible_capa(db, user, capa_id)`, `owners_ever(db, capa) -> set[int]`, `open_blockers(db, source_kind: str, source_id: int) -> int` (non-cancelled, non-verified count), `gate_enabled(db) -> bool`, `gate_errors(db, source_kind, source_id) -> list[str]` (empty when gate off or nothing blocks).

- [ ] **Step 1: Write failing tests:** happy path through all statuses with versions/events/notifications once each; creation validation (permission, due window, owner active+scope, title/text limits, source visibility, manual needs area); `test_verify_refused_for_current_or_past_owner_and_for_the_person_who_marked_it_done_even_if_admin`; `test_verify_by_second_person_ok_and_effectiveness_recorded_once`; `test_reopen_loop_counts_and_requires_reason`; `test_cancel_rules_and_cancelled_never_block`; `test_reassign_updates_owner_notifies_new_owner_and_history_keeps_old_owner_for_four_eyes`; `test_two_connections_race_complete_versus_cancel_exactly_one_wins`; `test_double_click_complete_makes_one_event`; `test_terminal_states_refuse_everything`; `test_visibility_owner_creator_scope_and_view_all_else_404`; `test_gate_helpers_default_off_and_on` (`open_blockers` counts only non-cancelled non-verified of that source).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with one `_transition` helper like `incident_flow`. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): CAPA with four-eyes verification and effectiveness check`.

### Task 6: Findings (one click) and the closing gate

**Files:** Modify `safety/patrol.py`, `safety/workflow.py`, `safety/incident_flow.py`; Test `tests/safety/test_capa_gate.py`, `tests/safety/test_patrol.py`

**Interfaces:**
- `patrol.finding_to_hazard(db, user, answer_id: int, hazard_type_id, source_id) -> tuple[int | None, list[str]]` (needs `patrol.perform`; answer must be `fail` in a **submitted** round visible to the user; builds the hazard through `workflow.report` with title = item text (≤ 200), description = section + note, area = round area; links `hazards.patrol_answer_id`; returns the existing hazard id if already created — idempotent; copies the answer's photos into the hazard as "before" photos).
- `patrol.finding_to_capa(db, user, answer_id: int, form: Mapping) -> tuple[int | None, list[str]]` (calls `capa.create(..., source=("patrol", answer_id))`; at most one per answer, idempotent).
- Gate hook: `workflow.close` and `incident_flow.close` call `capa.gate_errors(db, "hazard"|"incident", id)` after permission/status checks and before the transition; errors returned as-is.

- [ ] **Step 1: Write failing tests:** finding→hazard creates exactly one linked hazard (double call and two threads make one), refuses pass/na answers, draft rounds, other factory (404), missing/inactive type/source; photos copied; finding→CAPA idempotent and linked; `test_gate_off_by_default_closing_unchanged` (existing hazard/incident close tests still green); `test_gate_on_blocks_hazard_close_until_all_capas_verified_and_ignores_cancelled`; `test_gate_on_blocks_incident_close_message_names_remaining_count`; `test_capa_created_just_before_close_is_seen_by_the_gate` (same transaction ordering); `test_closed_hazard_still_celebrates_and_notifies_once_with_gate_on`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `pytest -q` → PASS.
- [ ] **Step 5:** Commit `feat(safety): one-click findings and CAPA closing gate`.

### Task 7: Screens, navigation, dashboard

**Files:** Create `safety/views/patrols.py`, `safety/views/capas.py`, templates `patrol_list.html`, `patrol_round.html`, `capa_list.html`, `capa_form.html`, `capa_detail.html`; Modify `safety/nav.py`, `safety/views/home.py`, `safety/templates/home.html`, `safety/app.py`; Test `tests/safety/test_patrol_views.py`, `tests/safety/test_nav.py`, `tests/safety/test_dashboard.py`, `tests/safety/test_theme_visual.py`

**Interfaces:** routes `GET /patrols` (rounds + "ถึงรอบตรวจ" plans), `POST /patrols/start`, `GET /patrols/<id>` (draft = answer form with large pass/fail/N-A radios and per-answer photo input; submitted = read-only summary with finding buttons), `POST /patrols/<id>/save|photo|submit`, `POST /patrols/answers/<id>/hazard|capa`, `GET /patrols/<id>/files/<att_id>` (whitelist + ownership, JPEG, `nosniff`); `GET /capas` (filters status/mine/overdue/q, 20 per page), `GET/POST /capas/new`, `GET /capas/<id>` with action forms, `POST /capas/<id>/start|complete|verify|reopen|cancel|reassign|effectiveness`; `GET/POST /admin/capa-settings` toggle for `require_capa_verified` (`criteria.manage`). Nav group "ตรวจความปลอดภัย & CAPA" (`/patrols` perm `patrol.perform`, `/capas` perm `capa.create`) plus admin items "แบบตรวจความปลอดภัย" `/admin/checklists`, "แผนการตรวจ" `/admin/patrol-plans` (`patrol.manage`), "ตั้งค่า CAPA" `/admin/capa-settings`. Dashboard: table "การตรวจความปลอดภัยล่าสุด" from `patrol.latest_rounds` (real data, empty state otherwise) and KPI card "CAPA เกินกำหนด" (`data-kpi="capa-overdue"`, own scope, links to `/capas?overdue=1`).

- [ ] **Step 1: Write failing tests:** round screen renders for inspector, 404 for others, submitted round has no inputs; save/submit through the screens incl. validation messages and photo requirement; findings buttons create then link to the hazard/CAPA and do not duplicate; CAPA list/filters/pagination, every action form only shown to the right people (verify hidden for past owners, shown with explanation otherwise); every POST needs CSRF + permission; XSS in all user fields (checklist/item text, notes, CAPA fields); photo serving whitelist and IDOR; nav per role (updated nav tests); dashboard table shows only real latest submitted rounds and the empty state; KPI count 6 and `capa-overdue` equals the DB; settings page toggles the gate; new pages added to the visual list in states (draft, submitted with finding, CAPA open/verified, admin pages).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (reuse `_uploads`, `_precheck`, `_int`, `_like` helpers by import). **Step 4:** Run → PASS incl. the visual job.
- [ ] **Step 5:** Commit `feat(safety): patrol and CAPA screens, navigation, dashboard`.

### Task 8: Whole-feature verification, docs

**Files:** Modify `README.md`; adjust whatever review/screenshots demand

- [ ] **Step 1:** Real-server walkthrough (Waitress, real SQLite) as admin, assessor (inspector), dept_head, plain reporter: create checklist + items + plan → plan due → inspector starts round on a phone-sized viewport (Playwright) → answers with a failed item + photo → submit → crafted POSTs on the submitted round (all refused) → finding to hazard and to CAPA (double-click once) → CAPA owner completes → a different assessor verifies, owner/admin-who-completed cannot → gate on: close hazard refused then allowed → scheduler run with simulated days produces the reminders once each (no flood) → dashboard table and KPI.
- [ ] **Step 2:** Screenshots 390 and 1280, both themes, for round (draft + submitted), patrol list, CAPA list/detail (open, done, verified), admin pages, dashboard; view them and fix concrete defects (touch target size ≥ 44 px on the answer radios).
- [ ] **Step 3:** Upgrade check from a Phase-6 database copy (tables, default rules, settings; old data intact; roles untouched).
- [ ] **Step 4:** README section "Safety Patrol และ CAPA (เฟส 7)" (flow, immutability, schedule rules, four-eyes, gate setting, permissions and the admin grant step). `pytest -q` green; commit `feat: Phase 7 docs and polish`; then one fresh whole-branch review and a fix pass.

---

## Self-Review

- **Spec coverage (§4):** admin-editable checklists with sections, pass/fail/N-A, required photo on fail, optional scoring → Tasks 1, 3, 4; binding to areas with schedule + reminders through `notify_rules` → Tasks 1, 2, 3; mobile-friendly rounds immutable after submit, pass/fail summary, dashboard "latest inspections" table → Tasks 4, 7; CAPA generic object with sources, owner, due date, statuses, four-eyes verification, effectiveness date, `capa_due_soon`/`capa_overdue` with L1–L3 escalation → Tasks 2, 5; failed item → hazard/CAPA in one click and the configurable closing gate → Task 6.
- **Type consistency:** `emit_capa/emit_patrol`, `plan_status`, `start_round/save_answers/submit_round`, `finding_to_hazard/finding_to_capa`, `capa.create/start/complete/verify/reopen/cancel/reassign/record_effectiveness`, `open_blockers/gate_errors/gate_enabled` are named identically across tasks and tests.
- **Review Focus** each maps to named tests (immutability → T1/T4, due calc → T2/T4, four-eyes → T5, gate → T6, findings → T4/T6, snapshot → T3/T4).
- **Proportion:** decisions, interfaces and test names; bodies left to the implementer. Deliberately out of scope until Phase 8: PDF/Excel exports of rounds and CAPA, report deadlines.
