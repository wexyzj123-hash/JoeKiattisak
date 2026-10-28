# AIAT Safety — Phase 3 (Risk Criteria + Hazard Register) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin-editable risk criteria (scales, levels, custom fields) and the hazard register with its workflow (report → assess → assign → close/reject), photos, audit trail, scope-aware visibility, dashboards, and a real Hub badge.

**Architecture:** A pure service layer (`safety/risk.py`, `safety/customfields.py`, `safety/workflow.py`, `safety/stats.py`) holds all rules and writes the audit trail in the same transaction; Flask views are thin. Risk level and score are **snapshotted onto each hazard at assessment**, so later criteria edits never rewrite history.

**Tech Stack:** Phase 1–2 stack plus Pillow (photo re-encoding). Existing code: `safety/{app,schema,master,perms,nav,security}.py`, `safety/views/*`, `tests/safety/helpers.py` (`login`, `make_user`, `sql`), `tests/conftest.py` (`safety_app`, `csrf`).

**Spec:** `docs/superpowers/specs/2026-10-28-aiat-safety-hub-design.md` (§4 risk criteria + hazard register, §5 workflow, §6 screens, §10 step 3). Earlier plans: Phase 1, Phase 2 (same folder).

## Global Constraints

- Thai UI, English identifiers; CSRF global; parameterised SQL; only code-defined table/column names are interpolated into SQL.
- Hazard statuses (exact keys): `reported`, `assessed`, `assigned`, `closed`, `rejected`. `closed`/`rejected` are terminal. Open = `reported|assessed|assigned`.
- Hazard code format `HZ-YYMM-####` (4-digit zero-padded sequence, restarts each month), unique.
- Dates are ISO `YYYY-MM-DD`; "today" comes from `safety.timeutil.today()` (monkeypatchable). A hazard is **overdue** when `due_date < today` and it is open (due today is not overdue).
- Risk: score = likelihood × severity. Defaults 5×5; scale size 2–10 per kind. Levels must cover every score `1..max_score` exactly once (no gap, no overlap). Default levels: ต่ำ 1–4 `#2ecc71` SLA 30 · ปานกลาง 5–9 `#f1c40f` SLA 14 · สูง 10–16 `#ff9f1c` SLA 7 · สูงมาก 17–25 `#ff4d4f` SLA 3, requires_approval.
- `requires_approval` level ⇒ the hazard cannot be closed by its assigned owner (four-eyes).
- Photos: JPEG/PNG/WebP only (decided by decoding, never by filename), ≤ 10 MB each, ≤ 5 per hazard per kind (`before`|`after`), re-encoded to JPEG ≤ 1600 px with EXIF stripped, stored as generated names under `safety/data/uploads/<hazard_id>/`.
- Visibility: a user with `hazard.assess|hazard.assign|hazard.close|master.view` sees every hazard inside their factory/department scope; any other user sees only hazards they reported. A hazard the user may not see answers **404**, never 403. Scope = the user's `factory_id`/`department_id` (empty = everything), matched through risk area → line → department → factory.
- `hazard_events` is append-only (database triggers reject UPDATE/DELETE). Existing permission keys are reused; no new keys.
- Pickers (`risk_area_options`) offer only active risk areas whose line, department and factory are all active (closes Phase 2 deferred M2).

## Review Focus

- Upload of a text file renamed `.jpg`, an SVG, a decompression-bomb image, a 0-byte file, and a filename like `../../x.jpg` → rejected or neutralised; nothing written outside the upload folder (Task 6).
- A user scoped to factory A requesting a hazard of factory B by id, and a reporter requesting someone else's hazard → 404 on page, photo and every POST (Task 7).
- Two assessors or two closers acting on the same hazard at once → exactly one transition wins, one audit event (Task 5).
- Criteria edit that leaves a score gap/overlap, or shrinks a scale below a value hazards already use → rejected; editing criteria after assessment leaves old hazards' level/score/color unchanged (Tasks 3, 5).
- Due date in the past, garbage, `2026-02-30`, more than a year ahead; overdue boundary (due today not overdue; yesterday overdue) (Tasks 5, 8).
- Hazard code across a month rollover and under concurrent creation never duplicates (Task 2).
- Custom field values: `nan`/`inf`, `<script>` text, a select value outside the options, a field deactivated after use (still shown on old hazards, escaped) (Tasks 4, 7).
- `UPDATE`/`DELETE` on `hazard_events` fails even with direct SQL (Task 2).
- Dashboard numbers exclude closed/rejected from "open" and respect the viewer's scope (Task 8).

## File Structure

```
safety/schema.py          modify: risk_scales, risk_levels, custom_fields, hazards, hazard_attachments, hazard_events (+triggers), seeds
safety/timeutil.py        create: today()
safety/risk.py            create: scales/levels engine
safety/customfields.py    create: field defs + validation
safety/hazards.py         create: code generation, visibility/scope, risk_area_options, queries
safety/images.py          create: photo validation/re-encode
safety/workflow.py        create: report/assess/assign/close/reject
safety/stats.py           create: dashboard queries
safety/views/{criteria,custom_fields,hazards,overview}.py    create
safety/views/{api,pages}.py, safety/nav.py                   modify
safety/templates/{criteria,custom_fields,hazard_list,hazard_form,hazard_detail,overview,overview_departments}.html
requirements.txt          modify: Pillow>=10
tests/safety/{test_risk,test_hazard_schema,test_criteria_admin,test_customfields,test_workflow,test_images,test_hazard_views,test_stats}.py
```

---

### Task 1: Risk criteria schema and engine

**Files:** Modify `safety/schema.py`; Create `safety/risk.py`; Test `tests/safety/test_risk.py`

**Interfaces:**
- Produces: tables `risk_scales(kind CHECK in 'likelihood'|'severity', level, label_th, description, PRIMARY KEY(kind, level))`, `risk_levels(id, name_th UNIQUE, min_score, max_score, color, sla_days, requires_approval, sort)`; `seed_risk(con)` (inserts the defaults only into empty tables, never overwrites edits; Thai scale labels 1–5: likelihood แทบไม่เกิด / เกิดได้ยาก / เกิดได้บางครั้ง / เกิดบ่อย / เกิดบ่อยมาก, severity เล็กน้อย / ปานกลาง / ร้ายแรง / รุนแรง / รุนแรงมาก); `ensure_schema` calls it.
  `risk.py`: `scale_size(db, kind: str) -> int`; `max_score(db) -> int`; `list_levels(db) -> list[Row]` (by `min_score`); `calc_score(db, likelihood: int, severity: int) -> int` (raises `ValueError` if either is outside `1..scale_size`); `level_for_score(db, score: int) -> Row | None`; `validate_levels(levels: list[dict], max_score: int) -> list[str]` (Thai errors: empty/duplicate name, color not `#RRGGBB`, `sla_days` not int ≥ 0, min/max outside `1..max_score` or min>max, overlap, gap — coverage must be exact).

- [ ] **Step 1: Write failing tests:** `test_defaults_seeded_5x5_with_four_levels_covering_1_to_25`; `test_seed_idempotent_and_keeps_edits`; `test_calc_score_and_out_of_range` (`calc_score(3,4)==12`; `0`, `6`, `-1` raise); `test_level_for_score_boundaries` (1,4→ต่ำ; 5,9→ปานกลาง; 10,16→สูง; 17,25→สูงมาก; 0 and 26 → `None`); `test_validate_levels_rejects_gap_overlap_range_color_sla_names` (one case each, plus a valid set returns `[]`); `test_max_score_follows_scale_sizes`.
- [ ] **Step 2:** `pytest tests/safety/test_risk.py -v` → FAIL.
- [ ] **Step 3:** Implement schema + `risk.py`.
- [ ] **Step 4:** Run → PASS; `pytest -q` all green.
- [ ] **Step 5:** Commit `feat(safety): risk criteria schema and engine`.

### Task 2: Hazard schema, audit trail, codes, visibility

**Files:** Modify `safety/schema.py`; Create `safety/timeutil.py`, `safety/hazards.py`; Test `tests/safety/test_hazard_schema.py`

**Interfaces:**
- Consumes: Task 1 tables; Phase 2 master tables and `user_permissions`.
- Produces: tables `hazards(id, code UNIQUE, title, description, risk_area_id, hazard_type_id, source_id, reported_by, reported_at, likelihood, severity, score, risk_level_id, level_name, level_color, status CHECK in the five keys, owner_id, due_date, closing_note, reject_reason, custom TEXT /*JSON*/, updated_at)`, `hazard_attachments(id, hazard_id, kind CHECK 'before'|'after', stored_name, orig_name, size, uploaded_by, uploaded_at)`, `hazard_events(id, hazard_id, at, actor_id, kind, from_status, to_status, note, data TEXT)` with triggers `BEFORE UPDATE`/`BEFORE DELETE` → `RAISE(ABORT, 'hazard_events is append-only')`.
  `timeutil.today() -> datetime.date`. `hazards.py`: `next_code(db, today: date | None = None) -> str` (`HZ-YYMM-####`, based on the highest existing sequence for that prefix; caller retries once on `IntegrityError`); `hazard_scope_sql(user) -> tuple[str, list]` (SQL fragment + args restricting `hazards h` to the user's factory/department via joins; empty scope ⇒ `("1=1", [])`); `can_view_all(perms: set[str]) -> bool`; `visible_hazard(db, user, hazard_id: int) -> Row | None` (None when missing, out of scope, or reporter-only user who is not the reporter); `risk_area_options(db, user) -> list[Row]` (`id, code, name_th, breadcrumb`; only chains where area, line, department, factory are all active and in the user's scope); `add_event(db, hazard_id, actor_id, kind, from_status, to_status, note="", data=None) -> None`.

- [ ] **Step 1: Write failing tests:** `test_hazard_tables_created_and_status_check` (insert status `bogus` → `IntegrityError`); `test_events_are_append_only` (UPDATE and DELETE via raw SQL raise); `test_next_code_format_and_sequence`; `test_next_code_restarts_next_month` (today monkeypatched via argument: 2026-01-31 → `HZ-2601-0001`, 2026-02-01 → `HZ-2602-0001`); `test_next_code_unique_under_retry` (pre-insert `HZ-2601-0001`, next is `…0002`); `test_visible_hazard_scope_and_reporter_rules` (factory-A user cannot see factory-B hazard; reporter-only role sees own but not others'; assessor sees all in scope; unscoped admin sees all); `test_risk_area_options_require_active_ancestors_and_scope` (deactivating factory, department, line or area each removes the option; out-of-scope area absent); `test_add_event_roundtrip_with_json_data`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): hazard schema, audit trail and visibility rules`.

### Task 3: Criteria admin screen (เกณฑ์ความเสี่ยง)

**Files:** Create `safety/views/criteria.py`, `safety/templates/criteria.html`; Modify `safety/app.py`, `safety/risk.py`; Test `tests/safety/test_criteria_admin.py`

**Interfaces:**
- Consumes: Task 1 engine; Task 2 `hazards` table; Phase 2 `permission_required`.
- Produces: `risk.apply_criteria(db, form: Mapping) -> list[str]` — one atomic save of the whole form: scale sizes (`likelihood_size`, `severity_size`, 2–10), labels/descriptions (`likelihood_label_<n>`, `severity_label_<n>`, `…_desc_<n>`), and levels (`level_name_<i>`, `level_min_<i>`, `level_max_<i>`, `level_color_<i>`, `level_sla_<i>`, `level_approval_<i>`, `level_count`). It validates against the **new** `max_score` with `validate_levels`, rejects shrinking a scale below any `likelihood`/`severity` already stored on a hazard, and applies nothing on error. Routes `GET/POST /admin/criteria` (permission `criteria.manage`).

- [ ] **Step 1: Write failing tests:** `test_page_requires_criteria_manage`; `test_save_labels_and_levels_ok`; `test_grow_scale_requires_levels_to_cover_new_max` (6×5 with old levels → error, nothing changed); `test_grow_scale_with_extended_levels_ok`; `test_gap_and_overlap_rejected_atomically` (DB unchanged after a rejected save); `test_shrink_below_used_value_rejected` (hazard with likelihood 5 → size 4 rejected); `test_shrink_allowed_when_unused`; `test_bad_inputs_rejected` (size 1/11/`abc`, color `red`, sla `-1`, blank name); `test_criteria_edit_does_not_change_existing_hazard_snapshot` (hazard row's `level_name/color/score` untouched after renaming/recoloring a level); `test_script_in_label_escaped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (single transaction; rebuild `risk_levels` ids is allowed because hazards keep snapshots, but keep `risk_level_id` valid by matching on name where possible). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): risk criteria admin screen`.

### Task 4: Custom fields per hazard type

**Files:** Modify `safety/schema.py`; Create `safety/customfields.py`, `safety/views/custom_fields.py`, `safety/templates/custom_fields.html`; Modify `safety/app.py`; Test `tests/safety/test_customfields.py`

**Interfaces:**
- Produces: table `custom_fields(id, hazard_type_id NULL /*NULL = every type*/, key, label_th, type CHECK 'text'|'number'|'select'|'date'|'checkbox', options TEXT /*one per line*/, required, sort, active)` with a unique index on `(COALESCE(hazard_type_id,0), key)`; `fields_for_type(db, hazard_type_id: int | None, include_inactive: bool = False) -> list[Row]` (type-specific + global, by `sort`); `validate_custom(fields: list[Row], form: Mapping) -> tuple[dict, list[str]]` reading `cf_<key>`: text ≤ 500 chars; number finite float (reject `nan`/`inf`); date valid ISO; select must be one of the options; checkbox → bool (required checkbox must be ticked); required enforced; unknown form keys ignored; returns `({key: value}, errors)`. Routes `GET/POST /admin/custom-fields` (permission `criteria.manage`): create (key `^[a-z][a-z0-9_]{1,31}$`, immutable afterwards, type immutable afterwards), edit label/options/required/sort, toggle active; select needs ≥ 2 options.

- [ ] **Step 1: Write failing tests:** `test_validate_each_type_valid_and_invalid` (parametrized: `nan`, `inf`, `1e999`, `2026-02-30`, select outside options, 501-char text, unticked required checkbox); `test_required_missing_reported_with_label`; `test_unknown_keys_ignored`; `test_fields_for_type_includes_global_and_excludes_inactive`; `test_admin_create_edit_toggle`; `test_key_and_type_immutable_after_create`; `test_duplicate_key_same_type_rejected_but_allowed_on_other_type`; `test_select_needs_two_options`; `test_page_requires_criteria_manage`; `test_script_in_label_escaped`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): custom fields per hazard type`.

### Task 5: Workflow service

**Files:** Create `safety/workflow.py`; Test `tests/safety/test_workflow.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4; `user_permissions`.
- Produces (all take an open `db` and the acting `user` row, return `list[str]` of Thai errors — empty means done; each commits atomically with its audit event; transitions use `UPDATE … WHERE id=? AND status IN (…)` and check `rowcount`, so a lost race returns an error and writes no event):
  `report(db, user, form: Mapping) -> tuple[int | None, list[str]]` — needs `hazard.report`; `title` 1–200, `description` ≤ 2000, `risk_area_id` from `risk_area_options`, active `hazard_type_id` and `source_id`, custom values via `validate_custom`; status `reported`; event `reported`.
  `assess(db, user, hazard_id, likelihood, severity, note="") -> list[str]` — `hazard.assess`; status `reported|assessed`; snapshots `score`, `risk_level_id`, `level_name`, `level_color`; event `assessed` with old/new values in `data`; status `assessed`.
  `assign(db, user, hazard_id, owner_id, due_date: str, note="") -> list[str]` — `hazard.assign`; status `assessed|assigned`; owner = active safety user; blank `due_date` ⇒ `today + level.sla_days`; else valid ISO date, `today ≤ date ≤ today+365`; event `assigned`; status `assigned`.
  `close(db, user, hazard_id, note) -> list[str]` — `hazard.close`; status `assigned`; note required ≤ 1000; if the hazard's level has `requires_approval`, the closer must not be the owner; event `closed`; stores `closing_note`.
  `reject(db, user, hazard_id, reason) -> list[str]` — `hazard.assess`; status `reported|assessed|assigned`; reason required ≤ 1000; event `rejected`.
  Every function first applies `visible_hazard` (not visible ⇒ error "ไม่พบรายการ") and the permission check.

- [ ] **Step 1: Write failing tests:** `test_report_creates_hazard_event_and_code`; `test_report_validation` (missing title, 201-char title, inactive area/type/source, area out of scope, area whose factory is inactive, bad custom value, no `hazard.report`); `test_assess_snapshots_score_and_level`; `test_assess_boundaries` (1×4=4 ต่ำ, 1×5=5 ปานกลาง, 5×5=25 สูงมาก; 0 and 6 rejected); `test_reassess_before_assign_logs_old_and_new`; `test_cannot_assess_after_assign_or_close`; `test_assign_default_due_from_sla`; `test_assign_due_date_validation` (past, `2026-02-30`, `abc`, today+366 rejected; today and today+365 accepted); `test_assign_owner_must_be_active_user`; `test_reassign_changes_owner_and_logs`; `test_close_requires_note_and_assigned_state`; `test_close_by_owner_blocked_when_level_requires_approval_but_allowed_otherwise`; `test_reject_from_each_open_state_and_terminal_states_locked`; `test_permissions_enforced_per_action`; `test_out_of_scope_hazard_is_not_found_for_every_action`; `test_lost_race_second_transition_fails_without_event` (simulate by changing status between the visibility read and the update); `test_each_success_writes_exactly_one_event_with_actor`; `test_criteria_edit_after_assessment_leaves_snapshot` (rename level, hazard keeps old `level_name`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Commit `feat(safety): hazard workflow service with audit trail`.

### Task 6: Photo handling

**Files:** Create `safety/images.py`; Modify `requirements.txt` (`Pillow>=10`); Test `tests/safety/test_images.py`

**Interfaces:**
- Produces: `class ImageError(Exception)` (message in Thai); `process_image(data: bytes) -> bytes` (decode with Pillow; allowed formats JPEG/PNG/WebP; reject empty, > 10 MB, undecodable, > 40 megapixels, animated; apply EXIF orientation then drop all metadata; flatten alpha onto white; downscale so the longest side ≤ 1600; return JPEG quality 82); `save_attachment(db, upload_root: str, hazard_id: int, kind: str, filename: str, data: bytes, user_id: int) -> int` (enforces `kind` in `before|after` and ≤ 5 per hazard per kind, writes `<upload_root>/<hazard_id>/<uuid4>.jpg`, stores a sanitised display name — basename only, control characters removed, ≤ 100 chars — never uses it as a path, returns the attachment id).

- [ ] **Step 1: Write failing tests:** `test_png_and_webp_and_jpeg_become_jpeg_within_1600px`; `test_text_file_named_jpg_rejected`; `test_svg_and_gif_and_bmp_rejected`; `test_empty_and_truncated_rejected`; `test_oversized_bytes_rejected`; `test_pixel_bomb_rejected` (e.g. 10000×10000 solid PNG); `test_exif_orientation_applied_and_metadata_stripped` (GPS tag absent in output); `test_alpha_flattened`; `test_filename_traversal_sanitised` (`../../x.jpg`, `a\\b\\c.png`, NUL and newline) with the stored file under `<upload_root>/<id>/` only; `test_sixth_photo_rejected_per_kind_but_other_kind_allowed`; `test_bad_kind_rejected`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): safe photo processing and storage`.

### Task 7: Hazard screens (ลงทะเบียนบันทึกอันตราย)

**Files:** Create `safety/views/hazards.py`, `safety/templates/{hazard_list,hazard_form,hazard_detail}.html`; Modify `safety/views/pages.py` (remove the `/hazards` placeholder), `safety/app.py` (register; set `MAX_CONTENT_LENGTH = 25 MB`, `UPLOAD_ROOT` default `safety/data/uploads`); Test `tests/safety/test_hazard_views.py`

**Interfaces:**
- Consumes: Tasks 2, 4, 5, 6.
- Produces: `GET /hazards` (filters `status`, `level`, `area`, `q` on code/title, `mine=1`; sortable by `code|reported_at|score|status|due_date` from a whitelist; 20 per page, clamped; shows the level color chip, due date, overdue flag); `GET/POST /hazards/new` (area/type/source selectors from `risk_area_options`/`active_options`; custom fields for the chosen type; up to 5 `before` photos); `GET /hazards/<id>` (details, snapshot level, custom values escaped — including fields since deactivated —, photos, audit timeline, and only the action forms the user may use in the current status); `POST /hazards/<id>/assess|assign|close|reject`, `POST /hazards/<id>/photos` (kind `before` for the reporter/assessors, `after` at close time); `GET /hazards/<id>/files/<att_id>` (image bytes, same visibility rule). Everything invisible → 404.

- [ ] **Step 1: Write failing tests:** `test_list_requires_login_and_hazard_permission_or_master_view`; `test_report_form_and_submit_with_photo_and_custom_field`; `test_reporter_sees_only_own_hazards_assessor_sees_scope`; `test_scoped_user_cannot_open_other_factory_hazard_page_photo_or_post` (404 each); `test_other_reporters_hazard_is_404`; `test_detail_shows_only_allowed_actions_per_status`; `test_full_flow_via_http` (report → assess → assign → close, each 302, timeline lists four events in order); `test_list_filters_search_sort_whitelist_and_page_clamp`; `test_xss_in_title_description_custom_value_and_filename_escaped`; `test_photo_route_serves_jpeg_only_for_visible_hazard`; `test_upload_rejects_fake_image_with_message_and_no_row`; `test_overdue_flag_boundary` (due today not flagged, yesterday flagged); `test_deactivated_custom_field_still_shown_on_old_hazard`; `test_post_without_permission_is_403_or_404_never_200`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): hazard register screens`.

### Task 8: Dashboards and the real Hub badge

**Files:** Create `safety/stats.py`, `safety/views/overview.py`, `safety/templates/{overview,overview_departments}.html`; Modify `safety/views/pages.py` (remove overview placeholders), `safety/views/api.py`, `safety/app.py`; Test `tests/safety/test_stats.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 5.
- Produces (all take `db` and `user`, honour `visible` rules, use `timeutil.today()`): `counts_by_status(db, user) -> dict[str, int]` (all five keys always present); `open_by_level(db, user) -> list[tuple[str, str, int]]` (name, color, count; unassessed open hazards counted under `("ยังไม่ประเมิน", "#8fa3bd", n)`); `overdue_count(db, user) -> int`; `heatmap(db, user) -> dict[tuple[int, int], int]` (likelihood, severity → open assessed hazards); `monthly_trend(db, user, months: int = 12) -> list[tuple[str, int, int]]` (`YYYY-MM`, reported, closed; zero-filled, oldest first); `by_department(db, user) -> list[Row]` (factory, department, open, overdue, high_or_above where "high" means a level with `requires_approval` or `max_score` ≥ 75 % of the max score). `GET /overview` (KPI tiles, level bars, 5×5 heat map using level colors, 12-month trend as inline SVG/CSS — no external libraries) and `GET /overview/departments` (table). `/api/summary` returns the real `{"open_hazards", "overdue"}` across all scopes (service token).

- [ ] **Step 1: Write failing tests:** `test_counts_include_all_status_keys_even_when_zero`; `test_open_excludes_closed_and_rejected`; `test_overdue_boundary_today_not_overdue_yesterday_overdue_and_closed_never`; `test_heatmap_cells_and_unassessed_excluded`; `test_trend_zero_filled_and_ordered_across_year_boundary`; `test_scope_limits_all_stats`; `test_by_department_counts`; `test_overview_pages_render_and_require_login`; `test_summary_api_returns_real_counts` (token required, counts match); `test_hub_badge_sums_summary` (reuse Phase 1 `fetch_badge` against the real endpoint via a monkeypatched `urlopen`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): dashboards and live summary API`.

### Task 9: Sidebar entries, docs, end-to-end check

**Files:** Modify `safety/nav.py`, `safety/views/pages.py`, `safety/templates/coming_soon.html`, `README.md`, `tests/safety/test_nav.py`

**Interfaces:**
- Produces: sidebar group **ผู้ดูแลระบบ** gains เกณฑ์ความเสี่ยง `/admin/criteria` and ฟิลด์เพิ่มเติม `/admin/custom-fields` (both `criteria.manage`) and การแจ้งเตือน `/admin/notifications` (`users.manage`, placeholder "เปิดใช้งานในเฟส 4"); `/hazards` item permission becomes `hazard.report`; order in the group: ประเภทผู้ใช้งาน, ผู้ใช้งาน, เกณฑ์ความเสี่ยง, ฟิลด์เพิ่มเติม, การแจ้งเตือน.

- [ ] **Step 1:** Update `test_nav.py` expectations (new order/items; `test_notifications_placeholder_200_and_mentions_phase_4`) → FAIL.
- [ ] **Step 2:** Implement; update README (risk criteria, custom fields, workflow, photo rules, visibility rules, Pillow). **Step 3:** Full `pytest -q` → all PASS.
- [ ] **Step 4:** Manual check on the real servers (walk **every** page including edit/detail pages, not just lists): admin edits criteria; creates a custom field for a hazard type; a reporter reports a hazard with a photo; an assessor assesses; the assigner assigns; a second user closes; the Hub launcher badge shows the open count; the scoped user cannot see another factory's hazard.
- [ ] **Step 5:** Commit `feat(safety): sidebar entries and Phase 3 docs`.

---

## Self-Review

- **Spec coverage:** §4 risk scales/levels/custom fields/hazards/attachments/events → Tasks 1–4; §5 workflow with audit trail → 5; photos "compressed like E-Checksheet" → 6; §6 ลงทะเบียนบันทึกอันตราย, ภาพรวม ×2, เกณฑ์ความเสี่ยง, ฟิลด์เพิ่มเติม → 3, 4, 7, 8, 9; Hub badge → 8; "versioned" criteria → snapshot on assessment (decision recorded in Architecture). Notifications and the mascot remain Phase 4 (placeholder entry only); CAPA/patrol/reports later.
- **Carry-over from earlier reviews:** Phase 2 M2 (selector ancestors) → Task 2; Phase 2 M6 (missing sidebar entries) → Task 9; Phase 1 "Safety never re-checks Hub status" and last-admin race remain open for Phase 4 hardening.
- **Type consistency:** `risk.calc_score/level_for_score/validate_levels/apply_criteria`, `customfields.fields_for_type/validate_custom`, `hazards.visible_hazard/risk_area_options/next_code/add_event`, and the `workflow.*` signatures are used identically by the view, stats and test tasks.
- **Review Focus:** every line is pinned to a named test in the owning task.
