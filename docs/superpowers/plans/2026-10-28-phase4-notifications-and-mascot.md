# AIAT Safety — Phase 4 (Notifications, Mascot, Theme, Hardening) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin-editable notifications (in-app bell + Telegram / LINE / Teams / Email with retry, quiet hours, L1–L3 escalation and a daily reminder job), the AIAT mascot "น้องเอเอียต" and a polished dark/light safety theme, plus the hardening items carried over from earlier reviews.

**Architecture:** Notifications are rows written **inside the same database transaction** as the workflow transition (in-app rows + an outbox of external messages) — no network call ever happens in a request. A separate delivery worker drains the outbox with a retry schedule and quiet hours; a once-a-day scheduler creates due/overdue reminders idempotently. All recipients, channels, lead times and escalation levels are **rows in `notify_rules`** edited from the UI. The mascot is a pure-Python inline-SVG library shared by both apps.

**Tech Stack:** Phase 1–3 stack; standard library only for delivery (`urllib`, `smtplib`, `threading`, `sqlite3` backup API); Playwright (already installed in this environment) for visual/responsive checks only.

**Spec:** `docs/superpowers/specs/2026-10-28-aiat-safety-hub-design.md` (§7 notifications, §8 theme and mascot, §9 backup/hardening, §10 step 4). Earlier plans: Phases 1–3 (same folder). **Pre-requisite:** the Phase 3 review fix pass is merged first; if it changes `workflow.py` signatures, adapt Task 3 to them.

## Global Constraints

- Event keys (exact): `hazard_reported`, `hazard_assessed`, `hazard_assigned`, `hazard_closed`, `hazard_rejected`, `due_soon`, `overdue`. Channel keys: `inapp`, `telegram`, `line`, `teams`, `email`.
- Recipient kinds: `reporter`, `owner`, `role:<key>`. A recipient must be an active Safety user; `role:` recipients must also be **in scope** of the hazard (user scope empty = all). The acting user is never notified about their own action. One notification per `(user_id, dedupe_key)`.
- Rule data model: `notify_rules(event, recipients, channels, offset_days, min_score, enabled)` — `offset_days` = days before due (`due_soon`) or days after due (`overdue`, ≥ 1); `min_score` (nullable) filters `hazard_assessed`. Escalation L1–L3 is expressed as several `overdue` rules with growing `offset_days` and wider recipients. Defaults seeded once, never overwritten.
- External delivery needs opt-in **and** a contact value on the user (`telegram_chat_id` `^-?\d{1,20}$`, `line_user_id` `^U[0-9a-f]{32}$`, `email` ≤ 254 chars with one `@`). Teams = one group webhook from the environment, used when a rule lists `teams`.
- Retry schedule (minutes): 1, 5, 15, 60, 180; the next failure marks the message `dead`. Statuses: `pending`, `sent`, `failed` (will retry), `dead`, `cancelled`.
- Quiet hours default 22:00–06:00 (window may cross midnight, start inclusive, end exclusive); external messages created inside it get `next_try_at` = window end; `urgent` (level `requires_approval`, or an `overdue` rule with `offset_days ≥ 3`) bypasses it. In-app is never delayed.
- Daily job default 07:30 local, runs at most once per date (`job_runs`), catches up at startup if the time has passed and today has not run.
- Secrets only from the environment (`TELEGRAM_BOT_TOKEN`, `LINE_CHANNEL_TOKEN`, `TEAMS_WEBHOOK_URL` (https only), `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`); never stored in the database, shown in the UI, written to logs, or included in error text. Outbound hosts are fixed (`api.telegram.org`, `api.line.me`) except the Teams URL and SMTP host (operator-controlled).
- Message bodies are plain text (no `parse_mode`, no HTML), control characters stripped, truncated (Telegram 4096, LINE 5000, Teams 20000, email subject 150); email headers are built with `email.message.EmailMessage` so a title can never inject headers.
- Hub status re-check: Hub `GET /api/user-status/<uid>` (header `X-Service-Token`) → `{"active": bool, "role": str}`; Safety caches per user for 60 s; Hub unreachable ⇒ **fail open** (logged once per outage).
- Mascot: original character "น้องเอเอียต" (hard-hat cartoon), inline SVG only — no external files, no `<script>`, no `href`/`url(` to other resources, `role="img"` + `<title>`; moods `default`, `alert`, `celebrate`, `sleeping`, `thinking`; size 16–512 px; animation only via CSS and disabled under `prefers-reduced-motion`.
- Theme: dark navy default, light via `prefers-color-scheme` and a manual toggle stored in `localStorage` inside try/catch; text contrast ≥ 4.5:1 in both themes; no horizontal page scroll at 390 px width.
- Backup: nightly SQLite online backup to `safety/data/backups/safety-YYYYMMDD.db`, keep the newest 14; uploads are documented as a separate backup target.

## Review Focus

- Daily job run twice, or the same transition emitted twice → still one notification per `(user, dedupe_key)` and one outbox row per channel (Tasks 2, 5).
- Recipient edge cases: deactivated user, user whose scope excludes the hazard, the actor, `role:` that no longer exists, owner unset (Task 2).
- Delivery failure modes: Telegram 403/429/500, timeout, SMTP auth failure, malformed contact; backoff, dead-letter, and never inside a request; secrets never appear in logs, rows or error text (Task 4).
- Quiet-hours edges: window crossing midnight, exactly at start and end, urgent bypass, `start == end` (Task 4).
- Content injection: titles containing newlines, `@everyone`, markdown/HTML, a 10 000-character title, and CRLF in an email subject (Task 4).
- Scheduler: two workers at once, a failing hazard in the middle of a run, missed days, a clock/DST change (Task 5).
- Rules admin misuse: unknown role, negative/huge offsets, duplicate rules, disabling every rule, a rule listing a channel nobody has (Task 7).
- Hub down → Safety stays usable; a Hub user disabled mid-session is signed out within 60 s (Task 8).
- Mascot SVG is well-formed XML with no script/external reference; pages have no horizontal scroll at 390 px (Tasks 9, 10).

## File Structure

```
shared/mascot.py              create: mascot_svg(mood, size)
shared/theme.css              modify: tokens, light/dark, components, reduced motion
shared/theme.js               create: theme toggle (try/catch storage)
safety/schema.py              modify: notifications, notify_rules(+seed), notify_outbox, app_settings, job_runs, user contact columns
safety/notify.py              create: recipients, templates, emit()
safety/delivery.py            create: transports, retry, quiet hours, deliver_due()
safety/scheduler.py           create: run_daily(), backup, background runner
safety/hubcheck.py            create: Hub status re-check (cached)
safety/workflow.py            modify: call emit() inside each transition
safety/views/{notifications,profile,notify_admin}.py   create
safety/views/{pages,users}.py, safety/{app,nav}.py     modify
hub/views/{api,auth,launcher}.py, hub/templates/*      create/modify: user-status API, mascot scenes
safety/run.py                 modify: start worker + scheduler
tests/{shared/test_mascot.py, safety/test_notify_*.py, safety/test_delivery.py, safety/test_scheduler.py, safety/test_bell.py, safety/test_notify_admin.py, safety/test_hubcheck.py, hub/test_user_status.py, safety/test_theme_visual.py}
```

---

### Task 1: Notification schema, settings and default rules

**Files:** Modify `safety/schema.py`; Test `tests/safety/test_notify_schema.py`

**Interfaces:**
- Produces: tables `notifications(id, user_id, event, title, body, link, hazard_id NULL, dedupe_key, created_at, read_at NULL, UNIQUE(user_id, dedupe_key))`, `notify_rules(id, event CHECK in the 7 keys, recipients, channels, offset_days, min_score NULL, enabled)`, `notify_outbox(id, channel CHECK, target, subject, body, status CHECK in the 5, attempts, next_try_at, last_error, urgent, notification_id NULL, dedupe_key, created_at, sent_at NULL, UNIQUE(channel, target, dedupe_key))`, `app_settings(key PK, value)`, `job_runs(job, run_date, finished_at, PRIMARY KEY(job, run_date))`; `safety_users` gains `telegram_chat_id`, `line_user_id`, `email`, `notify_channels` (comma list, default `inapp`); `seed_notify(con)` inserts default settings (`quiet_start=22:00`, `quiet_end=06:00`, `daily_time=07:30`) and default rules only when `notify_rules` is empty: `hazard_reported` → `role:assessor` inapp+telegram; `hazard_assessed` (min_score 17) → `role:dept_head,role:admin` inapp+telegram+line; `hazard_assigned` → `owner` inapp+telegram+line+email; `hazard_closed` → `reporter` inapp; `hazard_rejected` → `reporter` inapp; `due_soon` (offset 2) → `owner` inapp+telegram; `overdue` offset 1 → `owner`, offset 3 → `owner,role:dept_head`, offset 7 → `role:admin,role:dept_head` (all inapp+telegram+line+email).

- [ ] **Step 1: Write failing tests:** `test_tables_exist_and_checks` (bad event/channel/status → `IntegrityError`); `test_notification_unique_per_user_dedupe_key`; `test_outbox_unique_per_channel_target_key`; `test_default_rules_seeded_once_and_not_overwritten` (edit a rule, rerun `ensure_schema`, unchanged); `test_default_settings_present`; `test_existing_phase3_database_upgrades_in_place` (contact columns added, rows kept); `test_ensure_schema_idempotent`.
- [ ] **Step 2:** `pytest tests/safety/test_notify_schema.py -v` → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Commit `feat(safety): notification schema, settings and default rules`.

### Task 2: Recipient resolution and the in-app engine

**Files:** Create `safety/notify.py`; Test `tests/safety/test_notify_engine.py`

**Interfaces:**
- Consumes: Task 1 tables; Phase 3 `hazards`, `hazard_scope_sql` rules, `user_permissions`.
- Produces: `recipients_for(db, rule, hazard, actor_id: int | None) -> list[Row]` (active users only, deduplicated, scope-filtered for `role:`, actor excluded, unknown roles ignored); `render_message(event: str, hazard, extra: Mapping | None = None) -> tuple[str, str]` (Thai title/body — code, title, level and score, area, due date; control characters removed; each field truncated); `emit(db, event: str, hazard_id: int, actor_id: int | None, *, offset_days: int | None = None, today: date | None = None) -> int` — for every enabled rule of that event (and matching `offset_days` / `min_score`) creates one `notifications` row per recipient (`INSERT OR IGNORE` on `(user_id, dedupe_key)`; key = `<event>:<hazard_id>:<rule_id>:<offset|stage>`) and, for each external channel the rule lists that the user opted into with a valid contact, one `notify_outbox` row (`INSERT OR IGNORE`); `teams` creates one outbox row per rule+hazard with target `teams`. Returns the number of new notifications. Writes in the caller's transaction (no commit); wraps its work in a `SAVEPOINT` and swallows+logs any exception after rolling the savepoint back (notification problems must never break a transition).

- [ ] **Step 1: Write failing tests:** `test_reporter_and_owner_recipients`; `test_role_recipients_respect_scope` (assessor of factory B not notified about a factory-A hazard; unscoped assessor is); `test_actor_never_notified`; `test_inactive_user_and_unknown_role_ignored`; `test_owner_unset_yields_no_recipient`; `test_emit_creates_notification_with_link_and_thai_text` (link `/hazards/<id>`); `test_emit_twice_is_idempotent` (second call returns 0, no new rows); `test_min_score_filters_assessed_rule` (16 no, 17 yes); `test_external_rows_only_for_opted_in_users_with_valid_contact` (telegram bad id → no row; email without `@` → no row); `test_urgent_flag_for_requires_approval_level`; `test_teams_single_row_per_rule_and_hazard`; `test_disabled_rule_skipped`; `test_failure_inside_emit_is_swallowed_and_rolled_back` (monkeypatch a helper to raise → returns 0, caller's earlier writes intact); `test_message_sanitised_and_truncated` (title with `\n`, `\x00`, 10 000 chars).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): notification engine with recipients and in-app rows`.

### Task 3: Hook the engine into the workflow

**Files:** Modify `safety/workflow.py`; Test `tests/safety/test_notify_workflow.py`

**Interfaces:**
- Consumes: Task 2 `emit`.
- Produces: `report/assess/assign/close/reject` call `emit` (`hazard_reported`, `hazard_assessed`, `hazard_assigned`, `hazard_closed`, `hazard_rejected`) **before** their single `commit()`, so the notification rows are atomic with the transition; a lost race (`LOST_RACE`) emits nothing; `reassign` re-emits `hazard_assigned` with a new dedupe stage (`assigned:<owner_id>:<due_date>`).

- [ ] **Step 1: Write failing tests:** `test_each_transition_creates_expected_notifications` (reporter→assessors on report; dept_head/admin on a score ≥ 17 assessment; owner on assign; reporter on close/reject); `test_lost_race_emits_nothing`; `test_failed_validation_emits_nothing`; `test_rollback_removes_notifications_with_the_transition`; `test_reassign_notifies_new_owner_once_and_same_owner_again_is_deduped`; `test_emit_failure_does_not_block_transition` (monkeypatch `notify.emit` internals to raise → transition still succeeds, no notification); `test_actor_not_notified_for_own_action`; `test_phase3_workflow_tests_still_pass` (run `tests/safety/test_workflow.py` unchanged).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Commit `feat(safety): emit notifications from workflow transitions`.

### Task 4: Outbox delivery, transports, retry and quiet hours

**Files:** Create `safety/delivery.py`; Test `tests/safety/test_delivery.py`

**Interfaces:**
- Consumes: Task 1 `notify_outbox`, `app_settings`.
- Produces: `class TransportError(Exception)` (`.retryable: bool`); transports `send_telegram(chat_id, text, env)`, `send_line(user_id, text, env)`, `send_teams(url_target, subject, text, env)`, `send_email(addr, subject, text, env)` — each raises `TransportError` with a **secret-free** message; base URLs overridable by env (`TELEGRAM_API_BASE`, `LINE_API_BASE`) for tests; 10 s timeout; `RETRY_MINUTES = (1, 5, 15, 60, 180)`; `in_quiet_hours(now: datetime, start: str, end: str) -> bool` and `quiet_end_after(now, start, end) -> datetime`; `defer_if_quiet(db, now) -> int` (applies to freshly created non-urgent rows); `deliver_due(db, now: datetime, transports: Mapping | None = None, env: Mapping | None = None, limit: int = 50) -> dict` (claims due rows with an atomic `UPDATE … WHERE status IN ('pending','failed') AND next_try_at <= ?` so two workers never send one row twice; success → `sent`; retryable failure → `failed` with next `next_try_at`; non-retryable (403, 400, bad contact) or schedule exhausted → `dead`; unconfigured channel (missing token) → `dead` with reason "ช่องทางยังไม่ได้ตั้งค่า"; returns counts). `start_worker(app, interval: float = 30.0) -> threading.Thread` (daemon; one iteration per interval; exceptions logged, never raised).

- [ ] **Step 1: Write failing tests (fake transports unless stated):** `test_success_marks_sent`; `test_retryable_failure_backoff_schedule` (attempt 1→+1 min … 5→+180 min, sixth failure → `dead`); `test_non_retryable_goes_dead_immediately`; `test_unconfigured_channel_dead_with_reason_and_no_secret_in_error`; `test_two_workers_never_double_send` (claim race simulated with two connections); `test_quiet_hours_window_crossing_midnight` (22:00–06:00: 21:59 no, 22:00 yes, 05:59 yes, 06:00 no); `test_quiet_hours_same_day_window_and_start_equals_end_means_never`; `test_quiet_end_after_rolls_to_next_morning`; `test_urgent_bypasses_quiet_hours_and_inapp_unaffected`; `test_title_injection_sanitised_in_every_channel` (CRLF in email subject → single header, `@everyone`/HTML/markdown sent as plain text, 10 000-char title truncated per channel limit); `test_secrets_never_in_last_error_or_logs` (caplog); `test_telegram_http_transport_against_local_server` (`http.server` thread: 200 → sent; 403 → dead; 429 → retryable; 500 → retryable; timeout → retryable; request body contains no `parse_mode`); `test_line_http_transport_against_local_server`; `test_teams_requires_https_url_from_env`; `test_smtp_transport_against_local_aiosmtpd_substitute` (a minimal `socketserver` SMTP stub) including auth failure → dead; `test_worker_survives_exceptions`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): outbox delivery with retry, quiet hours and transports`.

### Task 5: Daily reminders, escalation, backup and the scheduler

**Files:** Create `safety/scheduler.py`; Modify `safety/run.py`; Test `tests/safety/test_scheduler.py`

**Interfaces:**
- Consumes: Tasks 2, 4; Phase 3 `timeutil.today`, hazards.
- Produces: `run_daily(db, today: date | None = None, force: bool = False) -> dict` — claims `job_runs('daily', today)` first (second call same day returns `{"skipped": True}` unless `force`); for every open hazard with a `due_date`: `due_soon` rules fire when `due_date - today == offset_days`, `overdue` rules when `today - due_date == offset_days`; each hazard handled in its own savepoint so one bad row never aborts the run; then `backup_database(db_path, backup_dir, today, keep=14)`; returns counts (`notifications`, `outbox`, `errors`, `backup`). `backup_database(...) -> str` uses `sqlite3.Connection.backup`, writes atomically (temp file → rename) and prunes to the newest 14. `due_run_needed(db, now: datetime) -> bool` (time ≥ `daily_time` and today not yet run — also covers catch-up). `start_scheduler(app, interval: float = 60.0) -> threading.Thread`. `safety/run.py` starts worker + scheduler once per process (not under the Flask reloader, not in tests).

- [ ] **Step 1: Write failing tests:** `test_run_daily_twice_same_day_skips_second`; `test_force_reruns_but_notifications_still_deduped`; `test_due_soon_fires_exactly_on_offset_day` (offset 2: day −3 no, −2 yes, −1 no); `test_overdue_levels_escalate_on_day_1_3_7_with_wider_recipients`; `test_closed_and_rejected_hazards_never_remind`; `test_due_today_is_not_overdue`; `test_one_failing_hazard_does_not_abort_run` (monkeypatch emit to raise for one id → others notified, `errors == 1`); `test_missed_days_do_not_backfill_old_stages` (job not run for 3 days → only today's stages fire, no flood); `test_due_run_needed_catch_up_and_before_time`; `test_backup_creates_valid_database_and_prunes_to_14`; `test_backup_atomic_no_partial_file_on_failure`; `test_concurrent_run_daily_only_one_wins` (two connections).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): daily reminders, escalation, backup and scheduler`.

### Task 6: Bell, notification centre and profile

**Files:** Create `safety/views/notifications.py`, `safety/views/profile.py`, `safety/templates/{notifications,profile}.html`; Modify `safety/templates/layout.html`, `safety/app.py` (context processor `unread_count`); Test `tests/safety/test_bell.py`

**Interfaces:**
- Consumes: Task 1 tables.
- Produces: `GET /notifications` (mine only, newest first, filter `unread=1`, 20 per page, page clamp); `POST /notifications/<id>/read` and `POST /notifications/read-all` (own rows only; another user's id → 404); `GET /notifications/go/<id>` (marks read then redirects to the stored internal link — only paths starting with a single `/`); `GET /notifications/unread-count` → `{"count": n}` (login required, polled every 60 s by the layout); bell with badge in the sidebar; `GET/POST /profile` (every logged-in user): contact fields and channel opt-in checkboxes, validated per Global Constraints; saving never touches role/scope.

- [ ] **Step 1: Write failing tests:** `test_list_shows_only_own_and_newest_first`; `test_unread_filter_and_pagination_clamped`; `test_mark_read_and_mark_all_read_only_affect_own`; `test_other_users_notification_is_404`; `test_go_redirects_to_internal_link_and_marks_read`; `test_go_never_redirects_off_site` (stored link `//evil.example` or `https://…` → `/notifications`); `test_unread_count_endpoint_and_bell_badge`; `test_anonymous_redirects_to_hub`; `test_profile_validates_contacts` (bad chat id / LINE id / email rejected, good accepted, cleared allowed); `test_profile_cannot_change_role_or_scope` (extra POST fields ignored); `test_xss_in_notification_text_escaped`; `test_empty_state_renders`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): notification bell, centre and profile`.

### Task 7: Admin notification settings screen

**Files:** Create `safety/views/notify_admin.py`, `safety/templates/notify_admin.html`; Modify `safety/views/pages.py` (remove the placeholder), `safety/app.py`; Test `tests/safety/test_notify_admin.py`

**Interfaces:**
- Consumes: Tasks 1, 4, 5. Permission: `users.manage`.
- Produces: `GET/POST /admin/notifications` with sections — **rules** (create/update/toggle/delete; validation: event in the 7 keys, recipients each `reporter|owner|role:<existing key>`, channels subset of the 5, `offset_days` int 0–60 (`overdue` ≥ 1, other events must be 0), `min_score` int ≥ 1 or blank, duplicate (event, recipients, channels, offset) rejected); **settings** (`quiet_start`, `quiet_end`, `daily_time` as `HH:MM`); **channel status** (configured yes/no from environment — never the values); **outbox monitor** (counts by status, last 50 non-sent rows with channel/target-masked/error, actions `retry` (dead/failed → pending now) and `cancel` (pending/failed → cancelled)); **tools** (`send_test` queues a message to my own contacts; `run_daily_now` calls `run_daily(force=True)`).

- [ ] **Step 1: Write failing tests:** `test_page_requires_users_manage`; `test_create_update_toggle_delete_rule`; `test_rule_validation_rejects_unknown_role_negative_huge_offsets_bad_channel_event` (each case); `test_overdue_offset_zero_rejected_and_other_events_nonzero_rejected`; `test_duplicate_rule_rejected`; `test_disabling_every_rule_allowed_but_warns`; `test_settings_validation_hhmm`; `test_channel_status_shows_configured_flags_never_secrets` (set fake token in env; token string absent from HTML); `test_outbox_retry_and_cancel_state_rules`; `test_targets_masked_in_outbox_view`; `test_send_test_queues_to_own_contacts_only`; `test_run_daily_now_is_idempotent_for_notifications`; `test_script_in_rule_fields_escaped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): notification settings admin screen`.

### Task 8: Hub status re-check and atomic last-admin guard

**Files:** Create `hub/views/api.py`, `safety/hubcheck.py`; Modify `hub/app.py`, `safety/app.py`, `safety/views/users.py`; Test `tests/hub/test_user_status.py`, `tests/safety/test_hubcheck.py`, `tests/safety/test_users.py`

**Interfaces:**
- Produces: Hub `GET /api/user-status/<int:uid>` (service token, constant-time; 401 without it) → `{"active": bool, "role": str}` (404 if unknown); `hubcheck.hub_user_active(app, hub_uid: int, now: float | None = None) -> bool` with a 60 s per-user cache, 2 s timeout, **fail open** on any error (logged once per outage); a Safety `before_request` signs out a session whose Hub user is inactive/missing (not for `/sso`, `/api/*`, static); `users.py` demote/disable of an admin becomes one guarded statement (`UPDATE … WHERE id=? AND (role<>'admin' OR active=0 OR EXISTS(SELECT 1 FROM safety_users WHERE role='admin' AND active=1 AND id<>?))`, check `rowcount`).

- [ ] **Step 1: Write failing tests:** `test_status_api_requires_token_and_reports_active_flag_and_role`; `test_status_api_unknown_user_404`; `test_disabled_hub_user_signed_out_within_cache_ttl` (clock injected); `test_cache_avoids_a_hub_call_per_request`; `test_hub_down_fails_open_and_logs_once`; `test_hub_slow_does_not_hang_request` (timeout); `test_sso_and_api_paths_not_rechecked`; `test_last_admin_guard_single_statement_blocks_second_demotion` (two connections demoting each other: exactly one succeeds); existing last-admin tests still pass.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: hub status re-check and atomic last-admin guard`.

### Task 9: The mascot library (น้องเอเอียต)

**Files:** Create `shared/mascot.py`; Test `tests/shared/test_mascot.py`

**Interfaces:**
- Produces: `MOODS = ("default", "alert", "celebrate", "sleeping", "thinking")`; `mascot_svg(mood: str = "default", size: int = 120, label: str = "น้องเอเอียต") -> str` — complete inline `<svg>` (viewBox 0 0 200 200) of an original hard-hat character (rounded body, safety helmet with reflective stripe, friendly eyes, mood-specific eyes/mouth/props: alert = raised hand + "!" badge, celebrate = confetti + raised arms, sleeping = closed eyes + "z", thinking = hand at chin + dots); unknown mood → `default`; `size` clamped to 16–512 (non-int → 120); `label` XML-escaped into `<title>`; no ids (so many on one page never collide); animations only through CSS classes defined in `theme.css`. Jinja global `mascot(...)` registered in both apps returning `Markup`.

- [ ] **Step 1: Write failing tests:** `test_every_mood_is_well_formed_xml` (`xml.etree.ElementTree.fromstring`); `test_has_role_img_title_and_viewbox`; `test_no_script_no_external_reference_no_event_handlers` (no `<script`, `href`, `xlink`, `url(`, `on\w+=`, `http`); `test_unknown_mood_falls_back_to_default`; `test_size_clamped_and_bad_size_defaults`; `test_label_is_escaped` (`<b>"&`); `test_moods_differ` (distinct markup per mood); `test_two_on_one_page_have_no_duplicate_ids` (no `id=` at all); `test_jinja_global_returns_markup_not_escaped` in both apps.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5 (visual):** with Playwright render each mood at 64 px and 200 px in light and dark backgrounds to PNGs under the scratchpad, **look at them**, and iterate on the drawing until each mood is clearly recognisable and friendly; note any tweaks in the commit message.
- [ ] **Step 6:** Commit `feat(shared): AIAT mascot SVG library`.

### Task 10: Theme polish, mascot placements, responsive checks, docs

**Files:** Modify `shared/theme.css`, create `shared/theme.js`; Modify `hub/templates/{base,login,setup,launcher}.html`, `safety/templates/{base,layout,home,hazard_list,hazard_detail,notifications,sso_error}.html`; `README.md`; Test `tests/safety/test_theme_visual.py`, `tests/hub/test_scenes.py`

**Interfaces:**
- Produces: theme tokens for dark/light (`data-theme="dark|light"` overrides `prefers-color-scheme`), a header toggle (🌓) persisted in `localStorage` in try/catch, `/static/theme.js` served by both apps; mascot placements: Hub login/setup scene (default), Hub launcher welcome (default, `alert` when the Safety badge > 0), Safety home greeting (default), empty states (`sleeping` on empty notification list/no hazards, `thinking` on no search results), overdue banner on the dashboard (`alert`), success flash after closing a hazard (`celebrate`), SSO error page (`thinking`); focus-visible outlines; `prefers-reduced-motion` disables animations.

- [ ] **Step 1: Write failing tests:** `test_hub_login_and_launcher_render_mascot` (`role="img"` present; badge > 0 ⇒ `alert` markup); `test_safety_empty_states_use_expected_moods`; `test_close_hazard_flash_uses_celebrate`; `test_overdue_banner_uses_alert_on_overview`; `test_theme_assets_served_by_both_apps` (`/static/theme.js`, `/static/theme.css` 200 with correct types); `test_pages_still_escape_user_text_with_mascot_labels`; Playwright tests marked `visual` (skipped with a clear reason if Chromium is unavailable): `test_no_horizontal_scroll_at_390px_on_key_pages` (Hub login/launcher; Safety home, hazard list/detail/new, overview, notifications, criteria, master list, users) using `document.documentElement.scrollWidth <= innerWidth`; `test_contrast_of_text_tokens_both_themes` (computed contrast ≥ 4.5 for body text, muted text, buttons, chips, flash); `test_theme_toggle_persists_and_survives_blocked_storage`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement CSS/JS/templates. **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Manual check on the real servers with screenshots at 1280 px and 390 px, dark and light, of every page group (look at them; fix anything cramped, clipped or low-contrast); walk **every** page including edit/detail pages; trigger a real notification chain end to end (report → bell appears for the assessor → outbox row created → worker marks it `sent` against a local stub server).
- [ ] **Step 6:** Update README (notification channels and env vars, rules model, quiet hours, scheduler, backup, mascot/theme). Commit `feat: theme polish, mascot placements and Phase 4 docs`.

---

## Self-Review

- **Spec coverage:** §7 in-app bell + Telegram/LINE/Teams/Email with retry, quiet hours, L1–L3 escalation, admin-editable rules, events (reported, high-risk assessed, assigned, due in N days, overdue escalates, closed) and a daily scheduler → Tasks 1–7; §8 dark navy + light theme, responsive, mascot with moods on login/empty states/toasts/Hub welcome → Tasks 9–10; §9 nightly backup → Task 5; carried-over hardening (Safety never re-checks Hub status; last-admin race) → Task 8. Document/report reminders (monthly/annual) remain with the later reports phase; the `notify_rules` model already supports adding such events.
- **Type consistency:** `emit`, `recipients_for`, `render_message`, `deliver_due`, `run_daily`, `backup_database`, `hub_user_active`, `mascot_svg` and the rule/outbox column names are used identically across tasks and tests.
- **Review Focus:** every line is pinned to a named test in the owning task.
- **Order/risk:** Tasks 1–3 make notifications atomic with workflow before any network code exists; Task 4 is the largest and security-sensitive (secrets, injection, SSRF-free fixed hosts); Task 10 includes real visual checks because earlier phases showed that page-level bugs slip past unit tests.
