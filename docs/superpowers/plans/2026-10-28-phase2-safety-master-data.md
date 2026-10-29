# AIAT Safety — Phase 2 (Master Data) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admin-editable master data in the Safety app — org hierarchy (โรงงาน → แผนก → ไลน์ → พื้นที่เสี่ยง), ประเภทอันตราย, แหล่งที่มา, editable roles/permissions, Safety users — with the sidebar shell from the screenshots.

**Architecture:** One generic master-data engine (`safety/master.py`) driven by table definitions renders list/create/edit/toggle for every table, so adding a new reference table is data, not new views. Permissions are keys stored on editable roles (`safety_roles`) and enforced server-side by `@permission_required`.

**Tech Stack:** unchanged from Phase 1 (Flask, SQLite, Jinja, pytest). Existing code: `safety/app.py`, `safety/schema.py`, `safety/security.py`, `safety/views/*`, `tests/conftest.py` (`safety_app`, `csrf`).

**Spec:** `docs/superpowers/specs/2026-10-28-aiat-safety-hub-design.md` (§4 data model, §6 screens). Phase 1 plan: `docs/superpowers/plans/2026-10-28-phase1-hub-sso-skeleton.md`.

## Global Constraints

- Thai UI text, English identifiers; CSRF on every POST (already global); parameterised SQL only; sort/filter column names come from a whitelist, never from user text.
- `ensure_schema(con)` stays idempotent and non-destructive; new columns on existing tables via guarded `ALTER TABLE`.
- Every master table has: `id, code, name_th, description, active DEFAULT 1, created_by, created_at, updated_at`. `code` is unique per table, case-insensitive, trimmed, 1–32 chars of `A-Za-z0-9_-`.
- Deactivate, never delete, master rows (history must stay readable). Selectors offer only `active=1` rows.
- List pattern (screenshots): search bar → table (ลำดับ, รหัส, ชื่อ, สถานะ, ผู้บันทึก, ดำเนินการ) with sortable headers and pagination, 20 rows/page.
- Default roles (keys): `admin` ผู้ดูแลระบบ, `assessor` ผู้ประเมิน/จป., `dept_head` หัวหน้าหน่วยงาน, `reporter` ผู้แจ้ง, `viewer` ผู้ดูข้อมูล. Phase 1 stored role keys `admin`/`reporter` in `safety_users.role` — unchanged.
- Permission keys: `master.view`, `master.edit`, `users.manage`, `roles.manage`, `hazard.report`, `hazard.assess`, `hazard.assign`, `hazard.close`, `criteria.manage`. `admin` always holds all keys and cannot lose `users.manage`/`roles.manage`.

## Review Focus

- `?sort=` / `?dir=` with SQL text or unknown column → falls back to default, never errors or injects (Task 3).
- `?page=0`, `?page=-1`, `?page=9999`, `?page=abc` → clamps to a valid page, 200 (Task 3).
- Code `ab-01` vs `AB-01` / `" ab-01 "` → duplicate rejected (Task 3).
- Name containing `<script>` and Thai combining characters renders escaped and searchable (Task 3).
- Create child under an inactive parent, or with a parent id that does not exist → rejected (Task 4).
- Deactivated parent's children still visible in lists but not offered in selectors (Task 4).
- Role in use cannot be deleted; `admin` role cannot lose permissions; unknown permission keys rejected (Task 2).
- Last active admin cannot be demoted/disabled; user assigned a nonexistent role rejected (Task 5).
- User without `master.edit` gets 403 on POST even with a valid CSRF token (Tasks 2, 3).

## File Structure

```
safety/schema.py        modify: master tables, safety_roles, role seed, user scope columns
safety/perms.py         create: PERMISSIONS, user_permissions, permission_required
safety/master.py        create: TableDef registry + generic list/query/save/toggle engine
safety/views/master.py  create: routes for every registered table
safety/views/roles.py   create: ประเภทผู้ใช้งาน
safety/views/users.py   create: ผู้ใช้งาน
safety/views/pages.py   create: placeholder pages for later phases
safety/templates/{layout,master_list,master_form,roles,users,coming_soon}.html
tests/safety/{test_schema,test_perms,test_master,test_org,test_users,test_nav}.py
```

---

### Task 1: Schema and role seed

**Files:** Modify `safety/schema.py`; Test `tests/safety/test_schema.py`

**Interfaces:**
- Produces: tables `factories`, `departments(factory_id)`, `lines(department_id)`, `risk_areas(line_id)`, `hazard_types`, `hazard_sources` (columns per Global Constraints; `UNIQUE(code COLLATE NOCASE)`), `safety_roles(key PK, name_th, permissions TEXT /*comma list*/, is_system INTEGER)`; `safety_users` gains nullable `factory_id`, `department_id`. `seed_roles(con)` inserts the five default roles only when absent (never overwrites admin edits). `ensure_schema` calls both, is idempotent, and upgrades a Phase-1 database in place.

- [ ] **Step 1: Write failing tests:** `test_ensure_schema_twice_is_noop`; `test_default_roles_seeded_with_admin_all_permissions`; `test_seed_does_not_overwrite_edited_role` (edit `viewer` name, rerun, unchanged); `test_phase1_database_upgraded_in_place` (create old `safety_users` without scope columns + one row, run `ensure_schema`, row kept and columns exist); `test_code_unique_case_insensitive` (insert `ab-01` then `AB-01` into `hazard_types` → `IntegrityError`).
- [ ] **Step 2:** `pytest tests/safety/test_schema.py -v` → FAIL.
- [ ] **Step 3:** Implement `ensure_schema`, `seed_roles(con) -> None`; guard `ALTER TABLE` with `PRAGMA table_info`.
- [ ] **Step 4:** Run → PASS; run `pytest -q` → all green.
- [ ] **Step 5:** Commit `feat(safety): master data schema and default roles`.

### Task 2: Permissions and role management

**Files:** Create `safety/perms.py`, `safety/views/roles.py`, `safety/templates/roles.html`; Modify `safety/app.py`; Test `tests/safety/test_perms.py`

**Interfaces:**
- Consumes: Task 1 `safety_roles`; Phase 1 `g.user`, `safety_login_required`.
- Produces: `PERMISSIONS: dict[str, str]` (key → Thai label, the nine keys above); `user_permissions(db, user) -> set[str]`; `permission_required(*keys)` decorator (401→redirect to Hub when anonymous, 403 when any key missing); routes `GET/POST /admin/roles` (permission `roles.manage`): create custom role (key `^[a-z][a-z0-9_]{1,31}$`), edit name/permissions, delete custom role. Rules: unknown permission keys rejected; system roles cannot be deleted; a role assigned to any user cannot be deleted; `admin` role always keeps all keys (edits to it are ignored/rejected).

- [ ] **Step 1: Write failing tests:** `test_admin_has_all_permissions`; `test_viewer_lacks_master_edit`; `test_permission_required_403_and_anonymous_redirect`; `test_create_role_with_permissions`; `test_unknown_permission_rejected`; `test_cannot_delete_system_role`; `test_cannot_delete_role_in_use`; `test_admin_role_permissions_cannot_be_reduced`; `test_roles_page_forbidden_without_roles_manage`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): permissions and editable roles`.

### Task 3: Generic master-data engine (ประเภทอันตราย, แหล่งที่มา)

**Files:** Create `safety/master.py`, `safety/views/master.py`, `safety/templates/{master_list,master_form}.html`; Modify `safety/app.py`; Test `tests/safety/test_master.py`

**Interfaces:**
- Consumes: Task 2 `permission_required`.
- Produces: `@dataclass TableDef(name, slug, label_th, parent: ParentDef | None = None)`; `REGISTRY: dict[str, TableDef]` containing `hazard-types` (`hazard_types`, "ประเภทอันตราย") and `hazard-sources` (`hazard_sources`, "แหล่งที่มา") — Task 4 adds the org tables; `list_rows(db, td, q: str, sort: str, direction: str, page: int, per_page: int = 20) -> Page` (`Page(rows, page, pages, total)`; unknown `sort` → `code`, `direction` not `asc|desc` → `asc`; page clamped to `[1, pages]`; `q` matches code or name_th via `LIKE` with escaped wildcards); `save_row(db, td, row_id: int | None, form: Mapping, user_id: int) -> list[str]` returns Thai error messages (empty = saved); `toggle_row(db, td, row_id) -> None`; `active_options(db, td) -> list[Row]`. Routes (per slug): `GET /master/<slug>`, `GET/POST /master/<slug>/new`, `GET/POST /master/<slug>/<id>/edit`, `POST /master/<slug>/<id>/toggle`; view needs `master.view`, writes need `master.edit`; unknown slug → 404. "ผู้บันทึก" column shows creator name.

- [ ] **Step 1: Write failing tests** (parametrized over both slugs where noted): `test_list_requires_master_view`; `test_create_and_list_shows_creator`; `test_code_duplicate_rejected_case_and_whitespace` (`ab-01`, `AB-01`, ` ab-01 `); `test_invalid_code_rejected` (`""`, `"a b"`, 33 chars, `"ก"`); `test_name_required`; `test_edit_updates_and_keeps_code_unique`; `test_toggle_hides_from_active_options_but_row_stays_listed`; `test_search_matches_code_and_thai_name`; `test_search_escapes_like_wildcards` (`%`, `_`); `test_sort_whitelist_falls_back` (`sort=name_th;DROP TABLE x`, `sort=id`, `dir=sideways` → 200 and rows intact); `test_page_clamped` (`0`, `-1`, `9999`, `abc` → 200); `test_script_in_name_is_escaped`; `test_post_without_master_edit_is_403_even_with_csrf`; `test_unknown_slug_404`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; templates reuse the Phase-1 theme and render the list pattern from Global Constraints. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): generic master-data engine with hazard types and sources`.

### Task 4: Org hierarchy (โรงงาน, แผนก, ไลน์/กลุ่มงาน, พื้นที่เสี่ยง)

**Files:** Modify `safety/master.py`, `safety/templates/master_form.html`, `safety/templates/master_list.html`; Test `tests/safety/test_org.py`

**Interfaces:**
- Consumes: Task 3 engine.
- Produces: `@dataclass ParentDef(column, table, label_th)`; registry slugs `factories`, `departments` (parent `factory_id`), `lines` (parent `department_id`), `risk-areas` (`risk_areas`, parent `line_id`). Forms show a parent `<select>` of active parents; lists show the parent name column. `save_row` rejects a missing, nonexistent, or inactive parent with a Thai message. `breadcrumb(db, td, row_id) -> str` returns `โรงงาน › แผนก › ไลน์ › พื้นที่` names for display (used by later phases).

- [ ] **Step 1: Write failing tests:** `test_full_chain_create_and_breadcrumb` (factory→department→line→risk area, breadcrumb joined with ` › `); `test_child_requires_parent`; `test_child_with_nonexistent_parent_rejected`; `test_child_under_inactive_parent_rejected`; `test_inactive_parent_not_in_selector_but_children_still_listed`; `test_codes_unique_per_table_not_global` (same code in `factories` and `departments` allowed); `test_parent_column_shown_in_list`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement as data additions to `REGISTRY` plus parent handling in the engine. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): organisation hierarchy master data`.

### Task 5: Safety users (ผู้ใช้งาน)

**Files:** Create `safety/views/users.py`, `safety/templates/users.html`; Modify `safety/app.py`; Test `tests/safety/test_users.py`

**Interfaces:**
- Consumes: Task 1 columns, Task 2 `permission_required`, `PERMISSIONS`.
- Produces: `GET /admin/users` (permission `users.manage`) lists `safety_users` (ชื่อ, บทบาท, โรงงาน/แผนกที่รับผิดชอบ, สถานะ, เข้าครั้งแรก); `POST /admin/users` actions `set_role` (role must exist in `safety_roles`), `set_scope` (`factory_id`/`department_id` optional; department must belong to the factory when both given), `toggle`. The last active user holding the `admin` role cannot be demoted or disabled. Users appear only after their first SSO login (no create form).

- [ ] **Step 1: Write failing tests:** `test_users_page_requires_users_manage`; `test_set_role_ok_and_unknown_role_rejected`; `test_cannot_demote_or_disable_last_admin`; `test_can_demote_admin_when_another_exists`; `test_set_scope_department_must_match_factory`; `test_scope_can_be_cleared`; `test_toggle_user_blocks_next_sso_login` (reuses Phase 1 inactive-user 403).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): safety user management`.

### Task 6: Sidebar shell, placeholders, docs

**Files:** Create `safety/templates/layout.html`, `safety/views/pages.py`, `safety/templates/coming_soon.html`; Modify `safety/templates/{home,roles,users,master_list,master_form}.html` (extend `layout.html`), `README.md`, `shared/theme.css`; Test `tests/safety/test_nav.py`

**Interfaces:**
- Produces: `layout.html` sidebar with the three groups from the screenshots — **ระบบข้อมูลหลัก** (ภาพรวมดำเนินงาน `/overview`, ภาพรวมตามหน่วยงาน `/overview/departments`, พื้นที่เสี่ยง `/master/risk-areas`, ลงทะเบียนบันทึกอันตราย `/hazards`, แหล่งที่มา `/master/hazard-sources`, ประเภทอันตราย `/master/hazard-types`), **ข้อมูลองค์กร** (โรงงาน, แผนก, ไลน์/กลุ่มงาน), **ผู้ดูแลระบบ** (ประเภทผู้ใช้งาน `/admin/roles`, ผู้ใช้งาน `/admin/users`). `nav_items(user_permissions: set[str], current_path: str) -> list[Group]` hides items the user lacks permission for and marks the active one. `/overview`, `/overview/departments`, `/hazards` render `coming_soon.html` ("เปิดใช้งานในเฟส 3").

- [ ] **Step 1: Write failing tests:** `test_nav_items_hide_unpermitted`; `test_nav_marks_active_item`; `test_admin_sees_all_three_groups`; `test_viewer_sees_no_admin_group`; `test_placeholder_pages_200_for_logged_in_and_redirect_for_anonymous`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; update README (Phase 2 menus, role/permission model). **Step 4:** Full `pytest -q` → all PASS.
- [ ] **Step 5:** Manual check: run both apps, log in via Hub, walk every sidebar link as admin; create a factory→department→line→risk area, a hazard type and a source; deactivate one and confirm it vanishes from selectors but stays in the list. Commit `feat(safety): sidebar shell and placeholders`.

---

## Self-Review

- **Spec coverage (§4 org structure, hazard types/sources, roles, users; §6 menu + list pattern; §10 step 2):** schema/roles → 1; permissions/roles UI → 2; list pattern, types, sources → 3; hierarchy → 4; users + scope → 5; menu → 6. Risk criteria, custom fields, hazards and notifications are Phases 3–4 (the sidebar shows their entries as placeholders only).
- **Type consistency:** `TableDef/ParentDef/REGISTRY`, `list_rows/save_row/toggle_row/active_options`, `permission_required/user_permissions/PERMISSIONS` are used identically across tasks.
- **Review Focus:** every line is pinned to a named test above.
- **Carry-over from Phase 1 review:** Safety does not yet re-check Hub user status (≤8 h session); `users.py` toggling is local only — Phase 4 hardening can add a Hub status check.
