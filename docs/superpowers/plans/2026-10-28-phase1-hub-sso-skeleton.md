# AIAT Safety + Hub — Phase 1 (Skeleton + SSO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable Hub (port 5000) with login, app launcher and app registry, and a runnable Safety app (port 5200) that accepts Hub SSO tokens — each with its own SQLite DB.

**Architecture:** Two independent Flask apps (`hub/`, `safety/`) plus a stateless `shared/` library holding SSO token sign/verify. Hub issues HMAC-signed 60 s tokens; Safety verifies, consumes the nonce once, and keeps its own users/roles.

**Tech Stack:** Python 3.10+, Flask>=3.0,<4, Waitress>=3.0, SQLite (WAL, `sqlite3.Row`), pytest. Same patterns as E-Checksheet v0.99.37 (`ecs/db.py`, `ecs/security.py`).

**Spec:** `docs/superpowers/specs/2026-10-28-aiat-safety-hub-design.md`

## Global Constraints

- Hub port 5000, Safety port 5200; DBs `hub/data/hub.db`, `safety/data/safety.db`.
- SSO token: HMAC-SHA256, expiry 60 s, single-use nonce, secret `SSO_SECRET` from `.env` (min 32 chars; app refuses to start otherwise). Constant-time compare.
- Service token for `GET /api/summary`: header `X-Service-Token`, value `SERVICE_TOKEN` from `.env`.
- Thai UI text, English identifiers. CSRF on every POST. Parameterised SQL only.
- Idempotent self-migrating `ensure_schema(con)` per app. `app_registry` rows are data: adding a system needs no code change.
- Hub role `admin` maps to Safety role `admin`; every other Hub role maps to `reporter`.

## Review Focus

- Token with tampered payload but old signature → rejected (Task 1).
- Token replayed a second time within 60 s → rejected (Task 4).
- Thai characters in `name` survive sign/verify round-trip (Task 1).
- `/login?next=https://evil.example` → never redirects off-site (Task 2).
- Hub user disabled after login → launcher/SSO redirect refuses (Task 3).
- Disabled app tile is hidden and `/go/<key>` returns 404 (Task 3).
- Safety `/api/summary` down or slow → Hub launcher still renders, no badge (Task 5).
- Missing/short `SSO_SECRET` → `create_app` raises `RuntimeError` (Tasks 2, 4).

## File Structure

```
shared/__init__.py
shared/sso.py            sign_token / verify_token / SsoError
shared/theme.css         safety theme CSS variables (tokens only, Phase-1)
hub/__init__.py  hub/app.py  hub/db.py  hub/schema.py  hub/security.py
hub/views/{__init__,auth,launcher,admin}.py
hub/templates/{base,login,setup,launcher,admin_apps,admin_users}.html
hub/run.py
safety/__init__.py  safety/app.py  safety/db.py  safety/schema.py
safety/views/{__init__,sso,home,api}.py
safety/templates/{base,home}.html   safety/run.py
tests/{conftest.py, shared/test_sso.py, hub/*, safety/*}
requirements.txt  requirements-dev.txt  .env.example  README.md
```

---

### Task 1: SSO token library

**Files:** Create `shared/__init__.py`, `shared/sso.py`, `requirements.txt`, `requirements-dev.txt`, `.env.example`; Test `tests/shared/test_sso.py`

**Interfaces:**
- Produces: `class SsoError(Exception)` with `.code` in `{"malformed","bad_signature","expired"}`;
  `sign_token(secret: str, claims: dict, ttl: int = 60, now: float | None = None) -> str` (adds `iat`, `exp`, `nonce` = `uuid4().hex`);
  `verify_token(secret: str, token: str, now: float | None = None) -> dict` (returns claims incl. `nonce`).
  Format: `b64url(json_utf8).b64url(hmac_sha256)`.

- [ ] **Step 1: Write failing tests** in `tests/shared/test_sso.py`: `test_roundtrip_returns_claims` (claims `{"uid":1,"name":"สมชาย ใจดี","roles":["admin"]}` come back equal, plus `nonce`); `test_tampered_payload_rejected` (swap payload with another valid-b64 payload, keep old signature → `code=="bad_signature"`); `test_wrong_secret_rejected`; `test_expired_rejected` (verify at `now=iat+61` → `code=="expired"`); `test_valid_at_59s`; `test_malformed_rejected` for `""`, `"abc"`, `"a.b.c"`; `test_nonces_unique`.
- [ ] **Step 2:** Run `pytest tests/shared/test_sso.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement `shared/sso.py` per the Interfaces; use `hmac.compare_digest`, `json.dumps(..., ensure_ascii=False, separators=(",",":"))`; add deps files and `.env.example` (`SSO_SECRET=`, `SERVICE_TOKEN=`, `HUB_URL=http://localhost:5000`, `SAFETY_URL=http://localhost:5200`).
- [ ] **Step 4:** Run tests → all PASS.
- [ ] **Step 5:** Commit `feat(shared): HMAC SSO token sign/verify`.

### Task 2: Hub app core — DB, first-admin setup, login/logout

**Files:** Create `hub/__init__.py`, `hub/app.py`, `hub/db.py`, `hub/schema.py`, `hub/security.py`, `hub/views/__init__.py`, `hub/views/auth.py`, `hub/templates/{base,login,setup}.html`, `tests/conftest.py`; Test `tests/hub/test_auth.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `create_app(test_config: dict | None = None) -> Flask` (config keys `DATABASE`, `SECRET_KEY`, `SSO_SECRET`, `SERVICE_TOKEN`, `SAFETY_URL`; raises `RuntimeError` if `len(SSO_SECRET) < 32`); `ensure_schema(con)` creating `users(id, username UNIQUE, name_th, password_hash, role CHECK in admin/supervisor/technician/viewer, active DEFAULT 1)` and `app_registry(id, key UNIQUE, name_th, url, icon, enabled DEFAULT 1, required_role DEFAULT '', sort DEFAULT 0)`; `hash_password/check_password`; `csrf_token()` + `@csrf_protect`; `@login_required`; `@role_required(*roles)`; `session["uid"]`.
  Routes: `GET/POST /setup` (only when `users` empty, creates admin; else 404), `GET/POST /login` (`?next=` honoured only for relative paths starting with a single `/`), `POST /logout`.

- [ ] **Step 1: Write failing tests** (`tests/conftest.py` gives `hub_app` fixture with temp DB and a 40-char test secret): `test_setup_creates_first_admin_then_404`; `test_login_ok_and_bad_password`; `test_login_blocks_open_redirect` (`next=https://evil.example` and `//evil.example` → redirect to `/`); `test_post_without_csrf_is_400`; `test_create_app_rejects_short_secret`; `test_disabled_user_cannot_login`.
- [ ] **Step 2:** Run `pytest tests/hub/test_auth.py -v` → FAIL.
- [ ] **Step 3:** Implement per Interfaces; copy WAL/busy_timeout/foreign_keys pragmas from `ecs/db.py`; password hashing `pbkdf2:sha256:600000` as in `ecs/security.py`; Thai labels on login/setup pages.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(hub): schema, setup, login/logout`.

### Task 3: Hub launcher + SSO redirect

**Files:** Create `hub/views/launcher.py`, `hub/templates/launcher.html`, `shared/theme.css`; Modify `hub/app.py` (register blueprint); Test `tests/hub/test_launcher.py`

**Interfaces:**
- Consumes: Task 1 `sign_token`; Task 2 `login_required`, `app_registry`.
- Produces: `GET /` launcher listing enabled apps the user's role may open (`required_role` empty = everyone; else role must match); `GET /go/<key>` → 302 to `<app.url>/sso?token=<t>` with claims `{"uid","name","roles":[user.role]}`; 404 if app missing/disabled; 403 if role not allowed; re-reads the user row so a disabled user is refused (clears session, redirects `/login`). `seed_default_apps(con)` inserts the Safety row (`key="safety"`, `name_th="ความปลอดภัย (Safety)"`, `url=SAFETY_URL`, `icon="shield"`) if absent; called from `create_app`.

- [ ] **Step 1: Write failing tests:** `test_launcher_lists_enabled_apps_only`; `test_disabled_app_404_on_go`; `test_go_redirects_with_verifiable_token` (decode with `verify_token`, assert uid/name/roles); `test_go_forbidden_for_wrong_role`; `test_disabled_user_session_refused_on_go`; `test_seed_is_idempotent`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; launcher is a tile grid using CSS variables from `shared/theme.css` (dark navy `--bg:#071426`, `--accent:#ff9f1c`, serve file via a `/static/theme.css` route). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(hub): app launcher and SSO redirect`.

### Task 4: Safety app — SSO consumption, session, own users

**Files:** Create `safety/__init__.py`, `safety/app.py`, `safety/db.py`, `safety/schema.py`, `safety/views/{__init__,sso,home}.py`, `safety/templates/{base,home}.html`; Test `tests/safety/test_sso_login.py`

**Interfaces:**
- Consumes: Task 1 `verify_token`, `SsoError`.
- Produces: `create_app(test_config=None) -> Flask` (config `DATABASE`, `SECRET_KEY`, `SSO_SECRET`, `SERVICE_TOKEN`, `HUB_URL`; same short-secret `RuntimeError`); `ensure_schema(con)` creating `safety_users(id, hub_uid UNIQUE, name, role DEFAULT 'reporter', active DEFAULT 1, created_at)` and `sso_nonces(nonce PRIMARY KEY, exp REAL)` (expired rows purged on each `/sso` call).
  Routes: `GET /sso?token=` (verify → insert nonce, `IntegrityError` ⇒ replay ⇒ 403 → upsert `safety_users` with role `admin` iff `"admin" in roles` on first creation only → `session["suid"]` → 302 `/`; bad/expired token → 403 Thai error page); `GET /` home requires session else 302 to `HUB_URL`; `POST /logout` clears session, 302 to `HUB_URL + "/logout"`; `@safety_login_required`, `@safety_role_required(*roles)` for later phases.

- [ ] **Step 1: Write failing tests:** `test_valid_token_logs_in_and_provisions_admin`; `test_non_admin_hub_role_becomes_reporter`; `test_replayed_token_403`; `test_expired_token_403`; `test_tampered_token_403`; `test_existing_user_role_not_overwritten_on_relogin` (change role in DB to `assessor`, log in again with `admin` claim → stays `assessor`); `test_inactive_safety_user_403`; `test_home_redirects_to_hub_when_anonymous`; `test_thai_name_stored`; `test_create_app_rejects_short_secret`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): SSO login, nonce replay protection, own users`.

### Task 5: Safety summary API + Hub badge

**Files:** Create `safety/views/api.py`; Modify `hub/views/launcher.py`, `hub/templates/launcher.html`, `safety/app.py`; Test `tests/safety/test_api.py`, `tests/hub/test_badges.py`

**Interfaces:**
- Produces: `GET /api/summary` on Safety → `{"open_hazards": int, "overdue": int}` (both `0` in Phase 1) when header `X-Service-Token == SERVICE_TOKEN` (constant-time), else 401. Hub function `fetch_badge(app_row, service_token: str, timeout: float = 2.0) -> int | None` (sum of the two counts; `None` on any error/timeout/non-200); launcher shows a badge only when value > 0.

- [ ] **Step 1: Write failing tests:** `test_summary_requires_token`; `test_summary_ok_with_token`; `test_fetch_badge_none_on_connection_error`; `test_fetch_badge_none_on_timeout` (monkeypatch `urllib.request.urlopen`); `test_launcher_renders_when_badge_fails`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with `urllib.request` (no new dependency). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: safety summary API and hub badge`.

### Task 6: Hub admin (users + app registry) and run scripts

**Files:** Create `hub/views/admin.py`, `hub/templates/{admin_apps,admin_users}.html`, `hub/run.py`, `safety/run.py`, `README.md`; Modify `hub/app.py`; Test `tests/hub/test_admin.py`

**Interfaces:**
- Consumes: Task 2 `role_required`, `csrf_protect`.
- Produces: admin-only routes `GET /admin/apps`, `POST /admin/apps` (create/update/toggle `enabled`, fields `key,name_th,url,icon,required_role,sort`; `key` must match `^[a-z0-9_-]{2,32}$`; `url` must start with `http://` or `https://`), `GET /admin/users`, `POST /admin/users` (create, set role, toggle `active`, reset password min 8 chars). Cannot disable or demote the last active admin. `run.py` loads `.env`, serves via Waitress on 5000 / 5200.

- [ ] **Step 1: Write failing tests:** `test_admin_pages_forbidden_for_non_admin`; `test_add_app_then_visible_on_launcher`; `test_rejects_bad_key_and_bad_url`; `test_cannot_disable_last_admin`; `test_create_user_min_password_8`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; README documents setup (`pip install -r requirements.txt`, `.env`, run both apps, first-admin `/setup`). **Step 4:** Run full suite `pytest -q` → all PASS.
- [ ] **Step 5:** Manual check: start both apps, log in at Hub, click Safety tile, land on Safety home as the same user; log out. Commit `feat(hub): admin pages and run scripts`.

---

## Self-Review

- **Spec coverage (Phase 1 = spec §2, §3, §9 SSO/security, §10 step 1):** layout → all tasks; app registry/launcher/SSO → 3; nonce/expiry/constant-time → 1, 4; badges via service token → 5; Hub user + registry admin → 6. Master data, risk criteria, hazards, notifications, mascot are Phases 2–4 (spec §10), intentionally absent. Theme tokens only (full theme + mascot in Phase 4).
- **Type consistency:** `sign_token/verify_token/SsoError`, `create_app`, `ensure_schema`, `fetch_badge`, session keys `uid` (Hub) / `suid` (Safety) are used identically across tasks.
- **Review Focus:** each line is pinned to a named test above.
