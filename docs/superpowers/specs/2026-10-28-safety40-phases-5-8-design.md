# Safety 4.0 — Phases 5–8 Design Spec (draft for review)

Date: 2026-10-28 · Builds on `2026-10-28-aiat-safety-hub-design.md` (Phases 1–4 done). Branch `claude/vibrant-knuth-8b04r0`.

## 1. What the user asked for (this round)

1. Restyle the dashboard like the supplied "Safety 4.0" reference (left blue sidebar, top bar, hero banner, KPI cards, charts, quick menu).
2. Use the **real AISIN mascot** (supplied sheet) instead of the drawn SVG character.
3. Next modules, in this order: **incident/accident + Near Miss**, **Safety Patrol + CAPA**, **reports/statistics (PDF/Excel monthly–yearly) + due-date reminders**.

### Decisions already made (from user answers)
- Mascot: **redraw it as vector SVG to look like the supplied AISIN mascot sheet** (decision updated on the user's latest message; no image files needed). Poses of the sheet: ยิ้มแย้ม, มั่นใจ, ใส่ใจงาน, สู้ไปด้วยกัน, รักษ์โลก, ปลอดภัยไว้ก่อน (white hard hat) — plus a sleeping variant for empty states. Checked visually against the reference at several sizes.
- Dashboard: **only widgets backed by real data**. No fake numbers. News, weather, e-Learning, factory map are **not drawn at all** (not even empty boxes) until their modules exist.
- Branding: **"Safety 4.0"** header + tagline "Safe People · Safe Workplace · Sustainable Future" + **AISIN** wordmark (drawn as vector lettering until a logo file is supplied).
- Phase 4 stays as built; its review fix pass has landed.

### Assumptions (correct if wrong)
- Light theme becomes the default (reference is light); dark stays available via the toggle. Brand colours: AISIN blue `#0a3a8f` family + green accent `#2e9e4f` for success; orange stays for warnings/actions.
- Same stack and patterns (Flask, SQLite, Thai UI, append-only audit trail, optimistic `version`, notification rules).
- Hub remains the SSO front door; new modules live inside the Safety app.

## 2. Phase 5 — "Safety 4.0" shell, dashboard and mascot assets (bounded; no new business data)

**Shell**
- Sidebar: blue gradient, white text, an **icon per menu item** (inline SVG set, no external files), active item as a white pill, AISIN wordmark + "Safety Today, Better Tomorrow" footer block.
- Top bar: Safety 4.0 title + tagline, **global search** (hazard code/title — real, scoped by the user's visibility rules), bell with unread count (existing), user menu (name, role, profile, logout), theme toggle. Phone: burger menu as today.
- Tokens: new light/dark sets; the contrast tests (≥ 4.5:1) and the real-browser tests (no horizontal scroll at 390 px, measured contrast, toggle) must keep passing and gain pages.

**Dashboard (`/` becomes "หน้าหลัก")** — each tile/chart uses existing data:
| Block | Data |
|---|---|
| Hero banner: slogan "ความปลอดภัยเริ่มได้จาก… ทุกคน", mascot (safety-first pose), 3 chips linking to real screens | static copy + links the user may open |
| 4 KPI cards: open hazards · overdue · closed this month · high-risk open | `stats.*` (scoped) |
| 12-month trend (reported vs closed) | `monthly_trend` |
| Donut: open hazards by hazard type | new `open_by_type(db,user)` |
| Table: latest hazards (code, area, level chip, status, due) | hazard list query |
| Quick menu grid (by permission) | nav items |
| My notifications (latest 5) | `notifications` |
| Risk heat-map link / `/overview` stays as the detailed view | existing |
"วันปลอดอุบัติเหตุ" (days without accidents) **appears only after Phase 6** (needs the incident module and a definition — see §6 Q1).

**Mascot (vector)**
- `mascot(mood, size, label, decorative=False)` keeps its API; moods: `default` (ยิ้มแย้ม, thumbs-up), `confident` (มั่นใจ, wink), `thinking` (ใส่ใจงาน, clipboard), `celebrate` (สู้ไปด้วยกัน, raised fist), `earth` (รักษ์โลก, holding a globe), `alert` (ปลอดภัยไว้ก่อน, white hard hat), `sleeping`. Character: chibi worker, navy cap with AISIN lettering, blue uniform with silver reflective stripes and chest patch, boots.
- `decorative=True` renders `aria-hidden` (no `role="img"`/title) for placements next to text (closes a Phase 4 accessibility finding).
- If real transparent image files are supplied later they can replace the vectors behind the same API; not part of Phase 5.

## 3. Phase 6 — Incident / accident and Near Miss

New module beside the hazard register (a hazard is a *condition*; an incident is an *event*).
- `incidents`: code `IN-YYMM-####`, kind `accident|near_miss`, occurred_at, risk area, description, immediate action, **severity** (near miss: potential level from the existing risk criteria; accident: first aid / medical treatment / lost time / fatality / property-only), people involved (name + employee no., optional, access-restricted), status `reported → investigating → actions_set → closed|rejected`, root-cause section (5-why text + cause category from an admin-editable list), link to a related hazard, photos (existing image pipeline), append-only events, `version` lock, notification events (`incident_reported`, `incident_assigned`, `incident_closed`, plus overdue reminders through the existing rules/scheduler).
- Admin-editable masters (existing engine): incident causes, injury severities, body parts (optional).
- Statistics: counts by kind/month/area/severity; **days without accident** (needs the definition below) shown on the dashboard KPI like the reference.
- Permissions: new keys `incident.report`, `incident.investigate`, `incident.close`; viewing people-involved data needs `incident.investigate`.

## 4. Phase 7 — Safety Patrol and CAPA

- **Checklists** (admin-editable templates: items grouped by section, each pass/fail/N-A, required photo on fail, scoring optional) bound to risk areas with a schedule (daily/weekly/monthly) — reminders reuse `notify_rules`.
- **Patrol round**: inspector opens a round for an area, answers items (mobile-friendly), submits; the round is immutable afterwards (append-only), result pass/fail summary — this is the "การตรวจความปลอดภัยล่าสุด" table in the reference.
- **CAPA** (corrective / preventive action) as a generic object: source = hazard | incident | patrol finding; owner, due date, status `open → in_progress → done → verified`, **verification by someone who was never the owner** (same four-eyes principle as hazards), effectiveness-check date; reminders and L1–L3 overdue escalation through the existing rule model (`capa_due_soon`, `capa_overdue`).
- A failed patrol item can create a hazard or a CAPA in one click; closing the hazard/incident requires its CAPAs verified (configurable).

## 5. Phase 8 — Reports and statistics

- Report definitions (monthly department report, annual summary, incident statistics, CAPA status) rendered from real data with the viewer's scope rules.
- Outputs **Excel (openpyxl)** and **PDF**; PDF library to be chosen in that phase's plan (pure-Python `reportlab` preferred: no system packages; Thai font must be bundled/licensed — to confirm).
- Submission deadlines: a `report_due` schedule table; reminders reuse notification rules (`report_due_soon`, `report_overdue`) and the daily scheduler; "mark as submitted" keeps an append-only record.

## 6. Open questions for the user (answer before Phase 6 planning)

1. **"วันปลอดอุบัติเหตุ"** — count days since the last: (a) lost-time accident, (b) any accident needing medical treatment, (c) any accident including first aid? Per factory or per department?
2. Are **injured-person names** allowed in the system (PII), or only employee numbers?
3. Is the incident severity scale company-defined (please send it) or should we use the default above?
4. PDF: is there a Thai company font (e.g. Sarabun/TH Sarabun) we may bundle?

## 7. Delivery plan

| Phase | Scope | Depends on |
|---|---|---|
| 4 fixes | Review findings of Phase 4 | — |
| 5 | Safety 4.0 shell, light theme, dashboard, global search, redrawn mascot, icon set, responsive tables | — |
| 6 | Incident + Near Miss, days-without-accident KPI | answers to §6 |
| 7 | Safety Patrol + CAPA | Phase 6 (incident source) |
| 8 | Reports/statistics, PDF/Excel, report deadlines | Phases 6–7 data |

Each phase: written plan → your review → inline execution with TDD, whole-branch fresh review, real-server walkthrough of **every** page (list, edit, detail), screenshots at 390 px and 1280 px in both themes.
