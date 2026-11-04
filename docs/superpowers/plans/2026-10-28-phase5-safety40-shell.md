# Safety 4.0 — Phase 5 (Shell, Dashboard, Mascot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle Safety and the Hub like the supplied "Safety 4.0" reference (blue shell with icons, top bar with search, hero banner, KPI cards, charts, quick menu), redraw the mascot to match the supplied AISIN mascot sheet, and finish the responsive-table work deferred from Phase 4 — using **only real data**.

**Architecture:** No new business tables. New pure helpers (`shared/icons.py`, `shared/brand.py`, extended `shared/mascot.py`, `safety/stats.py` additions, `safety/search.py`) feed templates; the shell is one `layout.html` + token-driven `theme.css`. Every visual claim is verified in a real browser (Playwright via Node) at 320/390/768/1280 px in both themes, and against the reference images by eye.

**Tech Stack:** Phase 1–4 stack; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-safety40-phases-5-8-design.md` §2 (decisions: real-data-only dashboard, "Safety 4.0" + AISIN branding, redrawn vector mascot). References: the two images the user supplied (AISIN mascot sheet; "Safety 4.0" dashboard).

## Global Constraints

- Real data only: no placeholder numbers, no empty "coming soon" widgets for news / weather / e-Learning / factory map / "days without accidents" (that KPI appears in Phase 6).
- Branding text exactly: title **Safety 4.0**, tagline **Safe People · Safe Workplace · Sustainable Future**, sidebar footer **Safety Today, Better Tomorrow**, AISIN wordmark drawn as inline vector lettering (no font dependency, no external files).
- Light theme is the default; dark when the system prefers dark or the user toggles (stored in `localStorage` in try/catch). Both theme blocks keep identical token names; every text/background pair ≥ 4.5:1 (extend `tests/shared/test_theme_tokens.py`); white text on the blue shell ≥ 4.5:1.
- Icons are inline SVG (24×24 viewBox, `stroke="currentColor"`), decorative ones `aria-hidden="true"`; icon-only buttons carry `aria-label`.
- Mascot moods (exact keys): `default`, `confident`, `thinking`, `celebrate`, `earth`, `alert`, `sleeping`; sizes 16–512 px; no `id=` attributes, no scripts, no external references; `decorative=True` ⇒ `aria-hidden="true"` and no `<title>`.
- Dashboard data respects the viewer's scope and visibility (reporter-only users see their own; scoped users only their factory/department) — reuse `stats._visible`. Queries are aggregate SQL (no per-row queries).
- Search (`/search?q=`): hazards only, `q` trimmed and capped at 100 chars, LIKE wildcards escaped, results = same visibility rules as `/hazards`, max 50 rows, HTML-escaped; empty/over-long/whitespace `q` shows the form with no results.
- Layout: no horizontal page scroll at 320 px; tables inside cards scroll or stack (`.table-stack` turns rows into labelled blocks below 640 px); inputs never narrower than 120 px in stacked rows; Thai long strings wrap.
- Keep all existing routes, permissions and test IDs working (766 tests must stay green except where a plan step says a test is updated).

## Review Focus

- Dashboard numbers equal the real data for each role (admin, scoped assessor, reporter-only), with an empty database, and with ~1 000 hazards (query count stays constant) (Task 5).
- Search: `%`, `_`, `\`, 101+ chars, `<script>`, another factory's hazard code, a reporter searching someone else's hazard → no leak, no 500 (Task 4).
- Mascot: every mood is recognisable at 48/96/200 px against the reference sheet; well-formed XML; label escaped; `decorative` variant has no accessible name (Task 1).
- Shell contrast and keyboard use: white-on-blue ≥ 4.5:1, focus visible on every interactive element, user menu (`<details>`) and mobile burger operable by keyboard, icon-only controls labelled (Tasks 2, 3).
- Narrow screens: 320 px page scroll, stacked tables usable (notification admin, users, custom fields, criteria), hero text readable, long Thai names (Task 6).
- Hub login/launcher match the new style and still pass the contrast/scroll checks (Task 7).
- A theme toggle with blocked storage still works; no flash of the wrong theme on load (Task 2).

## File Structure

```
shared/mascot.py          rewrite: AISIN-style character, 7 moods, decorative option
shared/icons.py           create: ICONS dict + icon(name, size=20, label=None)
shared/brand.py           create: aisin_wordmark(width), shield_logo(size), skyline(), TAGLINE constants
shared/theme.css          rewrite tokens (light default) + shell/dashboard/components
shared/theme.js           modify: no-flash apply, keep toggle
safety/nav.py             modify: icon per item
safety/stats.py           modify: open_by_type, closed_this_month, high_risk_open, latest_hazards
safety/search.py          create: search_hazards(db, user, q)
safety/views/{search,home}.py   create/modify
safety/templates/{layout,home,search}.html, partials/_table_stack macros   create/modify
hub/templates/{base,login,setup,launcher}.html   modify
tests/{shared/test_mascot.py,test_icons.py,test_brand.py,test_theme_tokens.py}, tests/safety/{test_search,test_dashboard,test_scenes,test_theme_visual}.py, tests/hub/test_scenes.py   modify/create
```

---

### Task 1: Redraw the mascot (AISIN style)

**Files:** Rewrite `shared/mascot.py`; Create `shared/brand.py` (AISIN lettering helper used on the cap/patch); Test `tests/shared/test_mascot.py`, `tests/shared/test_brand.py`

**Interfaces:**
- Produces: `MOODS = ("default","confident","thinking","celebrate","earth","alert","sleeping")`; `mascot_svg(mood="default", size=120, label="น้องเอเอียต", decorative=False) -> str` (same clamping/escaping/fallback rules as today; `decorative=True` → `aria-hidden="true"`, no `role`, no `<title>`); `brand.aisin_wordmark(width: int = 120, color: str = "currentColor") -> str` (inline `<svg>` with path lettering "AISIN" in wide italic bold, `role="img"` + `<title>AISIN</title>`), `brand.aisin_letters(x, y, w, fill) -> str` (path fragment reused inside the mascot's cap and chest patch). Allowed SVG tags: `svg, title, g, path, rect, circle, ellipse, line, polygon, defs`-free (no ids, no `<text>`). Pose ↔ mood: default = smile + thumbs-up; confident = wink + thumbs-up; thinking = clipboard in hands, eyes down-right; celebrate = eyes closed happy + raised fist + sparkles; earth = holding a globe with a leaf; alert = white hard hat, thumbs-up/pointing "safety first"; sleeping = eyes closed, "z" marks.
- Visual spec (from the reference): navy cap with AISIN lettering and white-edged brim, black hair, large dark eyes with white highlights, pink cheeks, cream skin, blue jacket with two silver reflective stripes across the chest and one on each sleeve, chest patch with AISIN lettering, pocket flaps, blue trousers, black boots with blue soles; chibi proportions (head ≈ 45 % of height).

- [ ] **Step 1: Write failing tests (replace the Phase 4 mascot tests where the mood list or tags changed):** `test_moods_are_the_expected_seven`; per mood: well-formed XML, `role="img"`, `<title>`, `viewBox="0 0 200 240"` (taller than before; assert exact value), class `mascot mascot-<mood>`; `test_no_script_no_external_reference_no_event_handlers` with the new allowed tag set; `test_decorative_has_no_accessible_name` (aria-hidden="true", no `role`, no `<title>`); `test_unknown_mood_falls_back_to_default`; size clamp and label-escape cases unchanged; `test_moods_differ`; `test_no_ids`; `test_coordinates_in_range`; `test_cap_and_chest_carry_the_aisin_lettering` (the lettering fragment's path data appears twice in every mood); `test_wordmark_is_valid_svg_scales_with_width_and_uses_currentColor`; `test_jinja_global_accepts_decorative` in both apps.
- [ ] **Step 2:** `pytest tests/shared -q` → FAIL.
- [ ] **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5 (visual, mandatory):** with Playwright render all 7 moods at 48/96/200 px on light and dark backgrounds, view the PNG **next to the supplied reference sheet**, and iterate (proportions, cap shape, stripes, face, hands) until each pose is clearly the same character; list the iterations in the commit message.
- [ ] **Step 6:** Commit `feat(shared): redraw mascot to match the AISIN sheet`.

### Task 2: Brand tokens, light default theme and the icon set

**Files:** Rewrite `shared/theme.css`; Modify `shared/theme.js`; Create `shared/icons.py`; Modify both `app.py` (register Jinja global `icon`), `tests/shared/test_theme_tokens.py`; Test `tests/shared/test_icons.py`

**Interfaces:**
- Produces: tokens (both themes, same names): `--bg --panel --panel-2 --line --text --muted --link --primary --primary-ink --primary-2 --shell-1 --shell-2 --shell-text --accent --accent-ink --danger --danger-ink --danger-text --ok --ok-text --warn-text --flash-bg --flash-line --success-bg --success-line --focus` (AISIN blue ≈ `#0b3b99`/`#1f6feb`, green accent for success, orange kept for warnings/highlights). `shell-1/2` are the sidebar gradient stops; `shell-text` white. `theme.js`: applies the stored/ system theme **before first paint** (script in `<head>`, no flash), toggle unchanged (try/catch storage). `shared/icons.py`: `ICONS: dict[str, str]` (path fragments for 24×24 stroke icons: `home, overview, departments, map-pin, clipboard, source, tag, building, layers, users, shield, sliders, puzzle, bell, user, search, sun-moon, menu, logout, chevron-down, plus, alert, check, clock, file`), `icon(name, size=20, label=None) -> Markup` (unknown name → empty circle icon; `label` ⇒ `role="img"` + `aria-label`, else `aria-hidden="true"`).

- [ ] **Step 1: Write failing tests:** `test_every_icon_is_valid_svg_with_currentColor_and_no_ids`; `test_icon_label_makes_it_accessible_and_decorative_hides_it`; `test_unknown_icon_falls_back`; `test_icon_size_clamped`; token tests: both themes define identical token names; contrast ≥ 4.5 for `text/muted/link` on `bg/panel/panel-2`, `primary-ink` on `primary` and `primary-2`, `shell-text` on `shell-1` and `shell-2`, `danger-ink` on `danger`, `text` on `flash-bg`/`success-bg`, `*-text` tokens on `bg/panel`; light is the default when no `data-theme` and no dark preference (assert the `:root` block is the light set and the dark set sits under `[data-theme="dark"]` + `@media (prefers-color-scheme: dark)`); `test_no_flash_script_is_in_head_and_runs_before_css_paint` (templates reference `/static/theme.js` before the stylesheet link? — assert the script tag precedes `<body>`); `test_theme_js_contains_try_catch_around_storage`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement tokens/CSS/JS/icons. **Step 4:** Run → PASS; run the existing visual tests (they must still pass; update expectations only for the intentionally changed default theme).
- [ ] **Step 5:** Commit `feat: Safety 4.0 brand tokens, light default theme, icon set`.

### Task 3: The shell — sidebar, top bar, user menu

**Files:** Modify `safety/nav.py`, `safety/templates/layout.html`, `shared/brand.py`, `shared/theme.css`; Test `tests/safety/test_nav.py`, `tests/safety/test_scenes.py`

**Interfaces:**
- Consumes: Task 2 `icon`, Task 1 `aisin_wordmark`, `shared/brand.py` `shield_logo(size) -> str`, `TAGLINE`.
- Produces: `nav.Item` gains `icon: str`; each menu item has one (home first: new item **หน้าหลัก** `/` pinned at the top for everyone who may see any module); layout: left gradient sidebar (brand block = shield logo + "Safety 4.0" + tagline; groups as today; active item = white pill; footer block "Safety Today, Better Tomorrow" + AISIN wordmark), top bar (page title area, search form `GET /search` with `aria-label`, bell with unread badge, theme toggle, **user menu** as `<details class="user-menu">` with name, role, profile link, logout form), mobile: burger as today but keyboard-operable; the hidden `nav-toggle` checkbox is removed from the tab order on desktop (`tabindex="-1"` while `min-width: 801px`, via CSS `display:none` on desktop).

- [ ] **Step 1: Write failing tests:** `test_nav_items_have_icons_and_home_first` (labels/hrefs updated: `หน้าหลัก /` first group item, all others unchanged, every item has a known icon); `test_nav_marks_home_active_only_on_root`; `test_shell_shows_brand_tagline_footer_and_wordmark` (exact strings from Global Constraints); `test_top_bar_has_search_bell_toggle_and_user_menu` (search form action `/search`, `aria-label`s, `<details class="user-menu">` contains name, role label, `/profile`, a logout form with CSRF); `test_icon_only_controls_are_labelled` (parse the page: every `<button>`/`<a>` without text has `aria-label`); `test_logout_still_works_from_user_menu`; update `test_home_and_admin_pages_use_the_shell`, `test_every_nav_link_resolves_for_admin`, `test_reporter_sees_only_hazard_registration_in_nav` for the new home item.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): Safety 4.0 shell with icon sidebar, top bar and user menu`.

### Task 4: Global search

**Files:** Create `safety/search.py`, `safety/views/search.py`, `safety/templates/search.html`; Modify `safety/app.py`; Test `tests/safety/test_search.py`

**Interfaces:**
- Consumes: `hazards.hazard_scope_sql`, `stats._visible` rules, `hazards.can_view_all`.
- Produces: `search_hazards(db, user, q: str, limit: int = 50) -> list[Row]` (code/title `LIKE` with escaped wildcards, newest first, same visibility as the hazard list); route `GET /search?q=` (permission: any hazard permission or `master.view`; anonymous → Hub redirect); page shows the query, result count and rows (code link, title, area, level chip, status) with the mascot `thinking` for no results.

- [ ] **Step 1: Write failing tests:** `test_search_matches_code_and_title_case_insensitive_and_thai`; `test_wildcards_are_literal` (`%`, `_`, `\\`); `test_query_trimmed_and_capped_at_100_chars` (a 5 000-char query → 200, no error, treated as its first 100 chars); `test_empty_or_whitespace_query_shows_form_without_results`; `test_result_limit_50_newest_first`; `test_scope_and_reporter_visibility_enforced` (other factory's code and other reporter's hazard never appear; counts match `/hazards`); `test_results_are_escaped` (`<script>` in title); `test_requires_login_and_permission`; `test_top_bar_search_submits_to_search`; `test_no_results_state_uses_thinking_mascot_decorative`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): global hazard search`.

### Task 5: Dashboard home (หน้าหลัก)

**Files:** Modify `safety/stats.py`, `safety/views/home.py`, `safety/templates/home.html`, `shared/brand.py` (`skyline()`), `shared/theme.css`; Test `tests/safety/test_dashboard.py`, `tests/safety/test_stats.py`

**Interfaces:**
- Consumes: Task 1 mascot (`alert` pose), Task 2 icons, existing `stats.*`.
- Produces: `stats.open_by_type(db, user) -> list[tuple[str, int]]` (hazard-type name, open count; top 5 + "อื่น ๆ" bucket; zero-count types omitted); `stats.closed_this_month(db, user, today: date | None = None) -> int` (from `hazard_events` kind `closed`, calendar month of `today`); `stats.high_risk_open(db, user) -> int` (open hazards whose snapshot `level_requires_approval = 1`); `stats.latest_hazards(db, user, limit: int = 6) -> list[Row]` (code, title, area name, level name/color, status, due_date, overdue flag); `brand.skyline() -> str` (decorative inline SVG). `GET /` renders: **hero** (gradient + skyline + slogan "ความปลอดภัยเริ่มได้จาก… ทุกคน" + sub-line + mascot `alert` decorative + 3 link chips: แจ้งอันตราย (if `hazard.report`), ทะเบียนอันตราย, ภาพรวมความเสี่ยง), **4 KPI cards** (เปิดอยู่ · เกินกำหนด · ปิดแล้วเดือนนี้ · ความเสี่ยงสูงที่เปิดอยู่; each links to the filtered list), **12-month trend** (bars reported/closed + cumulative-open line, server-side SVG, `role="img"` with a text summary and a visually-hidden data table), **donut** of open hazards by type (server-side SVG arcs, legend with counts and %), **latest hazards** table, **quick menu** grid (the user's permitted nav items with icons), **my notifications** (latest 5, link to centre). Empty database → friendly empty states with the `sleeping` mascot, never NaN/zero-division.

- [ ] **Step 1: Write failing tests:** stats: `test_open_by_type_counts_buckets_top5_and_excludes_closed_and_zero`; `test_closed_this_month_uses_event_month_and_scope` (event on the 31st/1st boundary, other month excluded); `test_high_risk_open_uses_snapshot_flag_and_ignores_closed`; `test_latest_hazards_order_limit_and_overdue_flag`; all four respect scope/reporter-only. Dashboard: `test_kpis_match_database_for_admin_scoped_assessor_and_reporter`; `test_dashboard_renders_with_empty_database_without_errors`; `test_donut_percentages_sum_to_100_and_handle_single_slice`; `test_trend_svg_has_summary_text_and_hidden_table_with_12_months`; `test_kpi_links_apply_filters` (`/hazards?status=…`); `test_quick_menu_only_permitted_items`; `test_my_notifications_only_own_latest_five`; `test_user_text_escaped_everywhere`; `test_query_count_constant_with_1000_hazards` (count statements via `sqlite3` trace: ≤ a fixed number independent of row count); `test_no_widgets_without_real_data` (page contains no "สภาพอากาศ", "e-Learning", "ข่าวสาร", "วันปลอดอุบัติเหตุ").
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(safety): Safety 4.0 dashboard home from real data`.

### Task 6: Responsive tables and forms

**Files:** Modify `shared/theme.css`, templates `safety/templates/{notify_admin,users,custom_fields,criteria,roles,master_list,hazard_list}.html` and `hub/templates/{admin_apps,admin_users}.html`; Test `tests/safety/test_theme_visual.py`

**Interfaces:**
- Produces: CSS `.table-stack` (≥ 640 px normal table; < 640 px each `<tr>` is a card, each `<td data-label="…">` shows its label above the value, action cells full width, inputs `min-width: 120px; width: 100%`); applied to every admin/list table; templates add `data-label` to every cell. The visual test gains widths 320 and 768 and a "stacked rows usable" probe (no input narrower than 120 px, no button outside the viewport).

- [ ] **Step 1: Extend failing tests:** visual: viewports 320/390/768/1280 × both themes × all pages; `test_no_horizontal_scroll_at_320px`; `test_stacked_rows_have_usable_controls` (inputs ≥ 120 px wide, buttons inside the viewport, labels visible) on notify admin, users, custom fields, criteria, roles; non-browser: `test_every_td_in_stacked_tables_has_a_data_label` (parse rendered pages).
- [ ] **Step 2:** Run → FAIL (notify admin currently squeezes inputs to ~20 px). **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: stacked responsive tables for narrow screens`.

### Task 7: Hub in the same style

**Files:** Modify `hub/templates/{base,login,setup,launcher}.html`, `shared/theme.css`; Test `tests/hub/test_scenes.py`, `tests/safety/test_theme_visual.py`

**Interfaces:**
- Produces: Hub login/setup = full-height split scene (brand panel with gradient, big mascot `default`, slogan, AISIN wordmark; form card on the right, stacked on phones); launcher = top bar (Safety 4.0 + user menu + theme toggle), welcome row with the mascot (`alert` when a badge > 0, else `default`, decorative), app tiles with icon, name, description line, badge; admin pages keep working.

- [ ] **Step 1: Write failing tests:** update `test_login_and_setup_render_the_mascot_scene` (mascot present, "Safety 4.0", wordmark); `test_launcher_tiles_show_icon_and_badge`; `test_launcher_user_menu_has_logout_with_csrf`; `test_hub_pages_have_labelled_icon_buttons`; visual: Hub pages pass 320/390/768/1280 scroll + contrast in both themes.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(hub): Safety 4.0 login and launcher`.

### Task 8: Visual verification against the references, docs, walkthrough

**Files:** Modify `README.md`; adjust CSS/templates as the review of screenshots demands

- [ ] **Step 1:** Run the visual job with `VISUAL_SHOTS`; **look at** dashboard (admin, reporter, empty DB), hazard list/detail, notifications, admin screens, Hub login/launcher at 1280 and 390, light and dark; compare the dashboard and mascot against the two reference images; list concrete differences and fix them (spacing, hero height, card shadows, chart legibility, icon weight, sidebar contrast).
- [ ] **Step 2:** Full `pytest -q` green. **Step 3:** Real-server walkthrough of **every** page (list, new, edit, detail, admin) as admin, scoped assessor and reporter; confirm no console errors (Playwright `pageerror`/`console`), search works end to end, user menu logout works.
- [ ] **Step 4:** README (branding, theme default, search, dashboard sources, stacked tables, mascot moods). **Step 5:** Commit `feat: Phase 5 visual polish and docs`.

---

## Self-Review

- **Spec coverage (§2 Phase 5):** shell/sidebar icons/top bar/search/user menu → Tasks 2–4; light default + AISIN branding → 2, 3, 7; dashboard (hero, KPIs, trend, donut, latest table, quick menu, my notifications) from real data → 5; mascot redrawn like the sheet + decorative a11y → 1; responsive-table deferral from Phase 4 → 6; Hub restyle → 7; reference comparison and real-browser verification → 8. "Days without accidents" explicitly excluded until Phase 6.
- **Type consistency:** `mascot_svg(...,decorative)`, `icon(name,size,label)`, `aisin_wordmark/aisin_letters`, `stats.open_by_type/closed_this_month/high_risk_open/latest_hazards`, `search_hazards` are used identically across tasks/tests.
- **Review Focus:** every line is pinned to a named test or an explicit visual step in the owning task.
- **Risk:** the mascot match is subjective — Task 1 Step 5 and Task 8 Step 1 require viewing the output beside the references; if the likeness is still weak the user can later swap in real transparent images behind the same API.
