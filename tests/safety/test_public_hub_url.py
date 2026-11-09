import pytest



@pytest.fixture
def lan(safety_app):
    safety_app.config["HUB_URL"] = "http://localhost:5001"
    return safety_app


def test_redirects_to_hub_use_the_host_the_user_came_in_on(lan):
    H = {"Host": "192.168.133.129:5200"}
    anon = lan.test_client()
    assert anon.get("/hazards", headers=H).headers["Location"] == "/login?next=/hazards"
    c = lan.test_client()
    with c.session_transaction(base_url="http://192.168.133.129:5200") as s:
        s["_csrf"] = "t"
    r = c.post("/logout", data={"_csrf": "t"}, base_url="http://192.168.133.129:5200")
    assert r.headers["Location"] == "http://192.168.133.129:5001"      # ออกจาก Safety → หน้า Hub ตามที่ผู้ใช้เปิดเข้ามา
    assert anon.get("/signout", headers=H).headers["Location"] == "/login"


def test_sso_error_page_links_back_to_hub_on_same_host(lan):
    html = lan.test_client().get("/sso?token=bad", headers={"Host": "192.168.133.129:5200"}).get_data(as_text=True)
    assert 'href="http://192.168.133.129:5001"' in html
