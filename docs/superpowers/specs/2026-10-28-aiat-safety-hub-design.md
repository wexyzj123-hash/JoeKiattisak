# AIAT Safety + Hub — Design Spec

Date: 2026-10-28 · Status: draft for review · Branch: `claude/vibrant-knuth-8b04r0`

## 1. Goal

Build a safety web system for recording and managing hazards in the plant, with all
reference data (areas, hazard types, sources, risk criteria) editable by admins from
the UI — no code changes. Safety is its own app; a central **Hub** web links to it
(and later to other systems) with single sign-on. UI is a modern safety theme with an
**AIAT mascot** (original cartoon character) and notifications for reports/documents.

### Decisions already made (from user)
- Architecture **A**: separate Flask apps — `hub/` and `safety/` — each with its own SQLite DB.
- **SSO via Hub**: login once at the Hub; Safety trusts a signed token, keeps its own roles.
- Phase-1 scope: **hazard registration + risk assessment**. CAPA, safety patrol, periodic
  reports are later phases (notification engine is built now so they plug in).
- No official mascot: design an original one (inline SVG).
- Reuse the stack/patterns of the existing E-Checksheet (v0.99.37): Flask, SQLite WAL,
  Waitress, Jinja, Thai UI, self-migrating schema, role checks, notify outbox, theme tokens.

### Assumptions (correct if wrong)
- Deployment is on the plant LAN (Windows server), HTTP(S) behind Waitress, like E-Checksheet.
- Hub users are the source of truth for identity; Safety maps them to its own roles.
- Thai is the primary UI language; English identifiers in code.

## 2. System layout

```
hub/      Flask app, port 5000   users, login, SSO issuer, app launcher, mascot welcome
safety/   Flask app, port 5200   hazard system (own DB data/safety.db)
shared/   small pure-python lib   sso token sign/verify, theme tokens, mascot svg, notify client
```
Each app runs standalone (`python hub/run.py`, `python safety/run.py`) and can later be
mounted under one port like `portal.py` does. `shared/` holds no Flask state.

## 3. Hub

- Pages: login, launcher (tiles per registered app with badge counts), user admin, app registry.
- **App registry** table (`apps`: key, name_th, url, icon, enabled, required_role) — adding a
  future system is a data change, not code.
- **SSO flow**: user clicks the Safety tile → Hub redirects to
  `safety/sso?token=<t>` where `t` = HMAC-SHA256 signed JSON `{uid, name, roles, iat, exp(60s), nonce}`
  using a shared secret from `.env` (`SSO_SECRET`). Safety verifies signature + expiry + nonce
  (single use), creates its own session, auto-provisions a `safety_users` row on first login.
- Logout at either app clears both (Hub exposes `/logout`; Safety redirects to it).
- Badge counts: Hub calls Safety `GET /api/summary` with a service token (`SERVICE_TOKEN`).

## 4. Safety data model (all admin-editable)

Org structure (matches screenshots): `factories → departments → lines → risk_areas`
(พื้นที่เสี่ยง). Each level: code, name_th, description, status (ใช้งาน/ไม่ใช้งาน), audit fields.

Master data: `hazard_types` (ประเภทอันตราย), `hazard_sources` (แหล่งที่มา).

Risk criteria (configurable, versioned):
- `risk_scales`: kind (`likelihood`|`severity`), level 1..N, label_th, description.
- `risk_levels`: name_th, min_score, max_score, color, `sla_days`, `requires_approval`.
- Score = likelihood × severity (rule fixed in v1; scale sizes N configurable, default 5×5).
- `custom_fields`: per hazard type — key, label_th, type (text/number/select/date/checkbox),
  options, required. Values stored as JSON on the hazard record.

Hazard register: `hazards`
`id, code (auto HZ-YYMM-####), title, description, risk_area_id, hazard_type_id,
source_id, reported_by, reported_at, likelihood, severity, score, risk_level_id,
status (reported|assessed|assigned|closed|rejected), owner_id, due_date, custom JSON`
plus `hazard_attachments` (photos, compressed like E-Checksheet imaging) and
`hazard_events` (append-only audit trail of every status/assessment change).

Users/roles: `safety_roles` (editable; ships with ผู้ดูแลระบบ, ผู้ประเมิน/จป., หัวหน้าหน่วยงาน,
ผู้แจ้ง, ผู้ดูข้อมูล) and per-role permission keys; `safety_users` (hub uid, role, factory/dept scope).

## 5. Hazard workflow

`reported` (any logged-in user, mobile-friendly form, photo upload) →
`assessed` (assessor sets likelihood/severity; level + SLA derive from criteria) →
`assigned` (owner + due date; high levels may require approval) →
`closed` (closing note + after-photo) · or `rejected` (with reason).
Every transition writes `hazard_events` and fires notifications (§7).

## 6. Screens (menu from screenshots)

- ระบบข้อมูลหลัก: ภาพรวมดำเนินงาน (KPI + risk heat-map + trend), ภาพรวมตามหน่วยงาน,
  พื้นที่เสี่ยง, **ลงทะเบียนบันทึกอันตราย**, แหล่งที่มา, ประเภทอันตราย
- ข้อมูลองค์กร: โรงงาน, แผนก, ไลน์/กลุ่มงาน
- ผู้ดูแลระบบ: ประเภทผู้ใช้งาน, ผู้ใช้งาน, **เกณฑ์ความเสี่ยง**, **ฟิลด์เพิ่มเติม**, **การแจ้งเตือน**
- List pattern as in screenshot: search bar → data table (ลำดับ/รหัส/ชื่อ/สถานะ/ผู้บันทึก/ดำเนินการ), pagination, sortable.

## 7. Notifications

Built on the E-Checksheet `notify.py` model: in-app bell (always) + outbox for Telegram / LINE /
Teams / Email with retry (1,5,15,60,180 min), quiet hours, L1–L3 escalation.
Rule table `notify_rules` (admin-editable): event, recipients (role/owner/dept head), channel,
lead time. Phase-1 events: new hazard reported, high-risk assessed, assigned to me, due in N days,
overdue (escalates), closed. A daily scheduler job generates due/overdue reminders.
Document reminders (monthly/annual reports) reuse the same rules in a later phase.

## 8. Theme and mascot

- Dark navy "safety control" theme + light mode, tokens as CSS variables (same mechanism as
  E-Checksheet `THEMES`), accent safety-orange/yellow, risk colors from `risk_levels`.
- Mascot **"น้องเอเอียต (AIAT)"**: original cartoon hard-hat character, inline SVG with moods
  (default, alert, celebrate, sleeping). Used on login scene, empty states, success/overdue
  toasts, and Hub welcome. Lives in `shared/mascot.py` so both apps use one source.
- Responsive; hazard report form optimized for phones.

## 9. Error handling, security, testing

- CSRF on all POST; role/permission decorators; parameterised SQL only; uploads size/type checked.
- SSO: constant-time signature compare, 60 s expiry, single-use nonce, secret only in `.env`.
- Self-migrating `ensure_schema` per app (idempotent), nightly SQLite backup.
- pytest per app: schema, SSO verify (valid/expired/replayed/tampered), risk scoring from
  configurable criteria, workflow transitions + permissions, notification rule firing.

## 10. Delivery plan (each phase tested before the next)

1. **Skeleton + SSO**: `shared/`, `hub/` login + launcher + app registry, `safety/` boots and accepts SSO.
2. **Safety master data**: org hierarchy, hazard types, sources, roles/users, CRUD UI pattern.
3. **Risk criteria + hazard register**: scales/levels/custom fields, report form, assessment, workflow, audit trail, dashboards.
4. **Notifications + mascot polish**: rules, bell, outbox channels, daily reminders, mascot states, theme.
5. *(Later)* CAPA, safety patrol, periodic reports/documents.

## 11. Out of scope for Phase 1
CAPA tracking, patrol routes, PDF/Excel report generation, SCADA/CMMS integration, native mobile app.
