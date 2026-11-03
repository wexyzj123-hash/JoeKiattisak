"""ตั้งค่าช่องทางแจ้งเตือน (SMTP/Teams) จากหน้าเว็บโดยผู้ดูแล: ความลับเก็บแบบเข้ารหัส ไม่แสดงกลับ และตัวส่งใช้ค่าจากหน้าเว็บ"""
import pytest

from safety import channel_config, delivery
from safety.db import connect
from tests.conftest import csrf
from tests.safety.helpers import login

URL = "/admin/notifications"
FORM = {"action": "channels_save", "smtp_host": "smtp.company.com", "smtp_port": "587", "smtp_tls": "starttls",
        "smtp_from": "safety@company.com", "smtp_user": "mailer", "smtp_password": "P@ss-w0rd-SECRET",
        "teams_webhook": "https://contoso.webhook.office.com/webhookb2/abc-SECRETHOOK", "teams_allowed_hosts": "", "http_timeout": "8"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD", "SMTP_TLS", "TEAMS_WEBHOOK_URL", "TEAMS_ALLOWED_HOSTS", "NOTIFY_HTTP_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)


def post(c, **kw):
    return c.post(URL, data={"_csrf": csrf(c), **{**FORM, **kw}})


def test_round_trip_save_effective_env_and_never_shown_back(safety_app):
    c = login(safety_app)
    assert post(c).status_code == 302
    db = connect(safety_app.config["DATABASE"])
    env = channel_config.effective_env(db)
    assert (env["SMTP_HOST"], env["SMTP_PORT"], env["SMTP_TLS"], env["SMTP_PASSWORD"]) == ("smtp.company.com", "587", "starttls", "P@ss-w0rd-SECRET")
    assert env["TEAMS_WEBHOOK_URL"].endswith("SECRETHOOK")
    raw = " ".join(r["value"] for r in db.execute("SELECT value FROM app_settings WHERE key LIKE 'chan.%'"))
    assert "P@ss-w0rd-SECRET" not in raw and "SECRETHOOK" not in raw and "smtp.company.com" in raw     # ความลับเข้ารหัส ที่เหลือเป็นข้อความปกติ
    html = c.get(URL).get_data(as_text=True)
    assert "P@ss-w0rd-SECRET" not in html and "SECRETHOOK" not in html and "ตั้งแล้ว — เว้นว่างเพื่อคงเดิม" in html and 'value="smtp.company.com"' in html


def test_blank_secret_keeps_old_and_clear_removes(safety_app):
    c = login(safety_app)
    post(c)
    post(c, smtp_password="", teams_webhook="", smtp_host="smtp2.company.com")
    db = connect(safety_app.config["DATABASE"])
    env = channel_config.effective_env(db)
    assert env["SMTP_PASSWORD"] == "P@ss-w0rd-SECRET" and env["SMTP_HOST"] == "smtp2.company.com" and env["TEAMS_WEBHOOK_URL"].endswith("SECRETHOOK")
    post(c, smtp_password="", teams_webhook="", clear_smtp_password="1", clear_teams_webhook="1")
    env = channel_config.effective_env(connect(safety_app.config["DATABASE"]))
    assert "SMTP_PASSWORD" not in env and "TEAMS_WEBHOOK_URL" not in env


def test_web_value_overrides_env_and_blank_falls_back_to_env(safety_app, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.from-env.local"); monkeypatch.setenv("SMTP_PORT", "25")
    c = login(safety_app)
    post(c, smtp_host="", smtp_port="2525")
    env = channel_config.effective_env(connect(safety_app.config["DATABASE"]))
    assert env["SMTP_HOST"] == "smtp.from-env.local" and env["SMTP_PORT"] == "2525"


@pytest.mark.parametrize("bad", [{"smtp_host": "http://x"}, {"smtp_host": "a b"}, {"smtp_port": "0"}, {"smtp_port": "99999"}, {"smtp_port": "x"},
                                 {"smtp_from": "not-an-email"}, {"smtp_tls": "weird"}, {"http_timeout": "0"}, {"http_timeout": "999"},
                                 {"teams_webhook": "http://contoso.webhook.office.com/x"}, {"teams_webhook": "https://evil.example.com/hook"},
                                 {"teams_webhook": "https://localhost/hook"}, {"teams_allowed_hosts": "bad host!"}, {"smtp_user": "a\nb"}])
def test_bad_input_rejected_and_nothing_saved(safety_app, bad):
    c = login(safety_app)
    post(c, **bad)
    assert connect(safety_app.config["DATABASE"]).execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE 'chan.%'").fetchone()[0] == 0


def test_allowed_hosts_extends_webhook_check_and_per_user_check(safety_app):
    c = login(safety_app)
    saved = lambda: channel_config.effective_env(connect(safety_app.config["DATABASE"])).get("TEAMS_WEBHOOK_URL", "")
    post(c, teams_webhook="https://prod-12.westus.logic.azure.com/workflows/x")                              # โดเมนมาตรฐานของ Microsoft
    assert saved().endswith("/workflows/x")
    post(c, teams_webhook="https://hooks.company.com/teams/x")
    assert saved().endswith("/workflows/x")                                                                  # โดเมนบริษัทยังไม่อนุญาต → ไม่เปลี่ยน
    post(c, teams_webhook="https://hooks.company.com/teams/x", teams_allowed_hosts="company.com")
    assert saved().endswith("/teams/x")
    from safety.notify import teams_host_ok
    assert teams_host_ok("https://hooks.company.com/y")                                    # ผู้ใช้รายบุคคลใช้โดเมนที่ผู้ดูแลอนุญาตด้วย


def test_requires_permission_and_csrf(safety_app):
    assert login(safety_app, "assessor").post(URL, data={**FORM}).status_code in (400, 403)
    c = login(safety_app)
    assert c.post(URL, data=FORM).status_code == 400
    login(safety_app, "assessor").get(URL).status_code == 403


def test_sender_uses_web_config(safety_app, monkeypatch):
    c = login(safety_app)
    post(c)
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, **kw): seen["addr"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): seen["tls"] = True
        def login(self, u, p): seen["login"] = (u, p)
        def send_message(self, m): seen["from"] = m["From"]
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    delivery.send_email("a@b.co", "หัวข้อ", "เนื้อหา", channel_config.effective_env(connect(safety_app.config["DATABASE"])))
    assert seen == {"addr": ("smtp.company.com", 587), "tls": True, "login": ("mailer", "P@ss-w0rd-SECRET"), "from": "safety@company.com"}


def test_tampered_or_foreign_key_value_reads_as_unset():
    channel_config.configure("x.db", "key-one")
    blob = channel_config.encrypt("secret value")
    assert channel_config.decrypt(blob) == "secret value" and blob != channel_config.encrypt("secret value")    # nonce สุ่ม
    assert channel_config.decrypt(blob[:-3] + "AAA") == ""
    channel_config.configure("x.db", "key-two")
    assert channel_config.decrypt(blob) == ""
