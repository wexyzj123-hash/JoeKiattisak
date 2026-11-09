"""shared/prefix.py — ให้แอปทำงานใต้ path นำหน้า (เช่น /safety-hub) หลังพร็อกซีที่ใช้พอร์ตร่วมกับระบบอื่น
พร็อกซีตัด prefix ออกก่อนส่งต่อ (หรือไม่ตัดก็ได้ — รับได้ทั้งสองแบบ); แอปเติม prefix ให้ลิงก์/ฟอร์ม/redirect/คุกกี้เอง
ไม่ตั้งค่า (ว่าง) = ไม่เปลี่ยนพฤติกรรมใด ๆ"""
from __future__ import annotations

import re

ATTR_RE = re.compile(r"""(\b(?:href|src|action|formaction)\s*=\s*)(["'])/(?!/)""")
FETCH_RE = re.compile(r"""(\bfetch\(\s*)(["'])/(?!/)""")


def normalize_prefix(value) -> str:
    value = (value or "").strip().strip("/")
    return f"/{value}" if value else ""


def install_prefix(app, prefix) -> str:
    prefix = normalize_prefix(prefix or app.config.get("URL_PREFIX"))
    if not prefix:
        return ""
    app.config["URL_PREFIX"] = prefix
    app.config["SESSION_COOKIE_PATH"] = prefix
    inner = app.wsgi_app

    def wsgi(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == prefix or path.startswith(prefix + "/"):
            environ["PATH_INFO"] = path[len(prefix):] or "/"
        environ["SCRIPT_NAME"] = prefix
        return inner(environ, start_response)

    app.wsgi_app = wsgi
    skip = (prefix + "/", prefix + "?")

    @app.after_request
    def add_prefix(response):
        loc = response.headers.get("Location")
        if loc and loc.startswith("/") and not loc.startswith("//") and not (loc == prefix or loc.startswith(skip)):
            response.headers["Location"] = prefix + loc
        if response.mimetype == "text/html" and not response.direct_passthrough and response.status_code != 304:
            text = response.get_data(as_text=True)
            marker = prefix + "/"

            def sub(m):
                return m.group(0) if text.startswith(marker, m.end() - 1) else f"{m.group(1)}{m.group(2)}{prefix}/"
            rewritten = FETCH_RE.sub(sub, ATTR_RE.sub(sub, text))
            if rewritten != text:
                response.set_data(rewritten)
        return response

    return prefix
