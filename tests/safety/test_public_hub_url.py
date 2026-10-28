import pytest



@pytest.fixture
def lan(safety_app):
    safety_app.config["HUB_URL"] = "http://localhost:5001"
    return safety_app


def test_redirects_to_hub_use_the_host_the_user_came_in_on(lan):
    H = {"Host": "192.168.133.129:5200"}
    anon = lan.test_client()
    assert anon.get("/hazards", headers=H).headers["Location"] == "http://192.168.133.129:5001"
    assert anon.get("/signout", headers=H).headers["Location"] == "http://192.168.133.129:5001/login"


def test_sso_error_page_links_back_to_hub_on_same_host(lan):
    html = lan.test_client().get("/sso?token=bad", headers={"Host": "192.168.133.129:5200"}).get_data(as_text=True)
    assert 'href="http://192.168.133.129:5001"' in html
