import re

from flask import Flask, jsonify, redirect, render_template_string, session

from shared.prefix import install_prefix, normalize_prefix


def make(prefix):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="k", SESSION_COOKIE_NAME="t")

    @app.route("/")
    def home():
        session["x"] = 1
        return render_template_string('<a href="/a">a</a><form action="/p" method="post"></form><link href="/static/t.css" rel="stylesheet">'
                                      "<a href='/q'>q</a><a href=\"https://x.test/y\">ext</a><a href=\"//cdn/z\">cdn</a><a href=\"rel/p\">r</a>"
                                      "<script>fetch('/notifications/unread-count')</script><a href=\"{{ url_for('home') }}\">u</a>")

    @app.route("/go")
    def go():
        return redirect("/dest?x=1")

    @app.route("/ext")
    def ext():
        return redirect("https://other.test/a")

    @app.route("/api")
    def api():
        return jsonify(link="/hazards/1")

    install_prefix(app, prefix)
    return app


def test_normalize_prefix():
    assert [normalize_prefix(x) for x in ("", "/", None, "safety", "/safety/", "/a/b/")] == ["", "", "", "/safety", "/safety", "/a/b"]


def test_no_prefix_changes_nothing():
    app = make("")
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'href="/a"' in body and app.test_client().get("/go").headers["Location"] == "/dest?x=1"


def test_html_links_redirects_and_cookie_path_are_prefixed_once():
    c = make("/safety-hub").test_client()
    r = c.get("/")                                                       # พร็อกซีตัด prefix ออกก่อนส่งต่อ
    body = r.get_data(as_text=True)
    for want in ('href="/safety-hub/a"', 'action="/safety-hub/p"', 'href="/safety-hub/static/t.css"', "href='/safety-hub/q'",
                 "fetch('/safety-hub/notifications/unread-count')", 'href="/safety-hub/"'):
        assert want in body, want
    assert 'href="https://x.test/y"' in body and 'href="//cdn/z"' in body and 'href="rel/p"' in body
    assert "/safety-hub/safety-hub" not in body                           # url_for ใส่ prefix แล้ว ไม่ซ้ำ
    assert "Path=/safety-hub" in r.headers["Set-Cookie"]
    assert c.get("/go").headers["Location"] == "/safety-hub/dest?x=1"
    assert c.get("/ext").headers["Location"] == "https://other.test/a"
    assert c.get("/api").get_json() == {"link": "/hazards/1"}              # JSON ไม่ถูกแก้
    assert int(r.headers["Content-Length"]) == len(r.get_data())


def test_request_that_still_carries_prefix_is_accepted():
    c = make("/safety-hub").test_client()
    assert c.get("/safety-hub/").status_code == 200 and c.get("/safety-hub/go").headers["Location"] == "/safety-hub/dest?x=1"
    assert c.get("/safety-hub").status_code == 200


def test_real_apps_work_under_a_prefix(tmp_path):
    from hub.app import create_app as hub_app
    from safety.app import create_app as safety_app
    secret = "v" * 40
    hub = hub_app({"TESTING": True, "DATABASE": str(tmp_path / "h.db"), "SECRET_KEY": "k", "SSO_SECRET": secret, "SERVICE_TOKEN": "s",
                   "SAFETY_URL": "http://h:5000/safety-hub/app", "URL_PREFIX": "/safety-hub"})
    html = hub.test_client().get("/login").get_data(as_text=True)
    assert "/safety-hub/static/theme.css" in html
    assert not re.search(r'(?:href|src|action)="/(?!safety-hub/)[^/"]', html), re.findall(r'(?:href|src|action)="/[^"]*', html)
    saf = safety_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k", "SSO_SECRET": secret, "SERVICE_TOKEN": "s",
                      "HUB_URL": "http://h:5000/safety-hub", "UPLOAD_ROOT": str(tmp_path / "up"), "URL_PREFIX": "/safety-hub/app"})
    r = saf.test_client().get("/")
    assert r.status_code == 302 and r.headers["Location"].startswith("http://h:5000/safety-hub")
