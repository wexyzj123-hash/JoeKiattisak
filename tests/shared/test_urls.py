import pytest

from shared.urls import public_url


@pytest.mark.parametrize("url,host,want", [
    ("http://localhost:5200", "192.168.133.129:5001", "http://192.168.133.129:5200"),
    ("http://127.0.0.1:5200/sso?x=1", "safety.company.local:5001", "http://safety.company.local:5200/sso?x=1"),
    ("http://localhost/", "10.0.0.5:5001", "http://10.0.0.5/"),
    ("http://0.0.0.0:5200", "10.0.0.5", "http://10.0.0.5:5200"),
    ("http://192.168.133.129:5000/echeck/dashboard", "10.0.0.5:5001", "http://192.168.133.129:5000/echeck/dashboard"),
    ("https://other.example.com:8443/a", "10.0.0.5:5001", "https://other.example.com:8443/a"),
    ("http://localhost:5200", "[fe80::1]:5001", "http://[fe80::1]:5200"),
    ("http://localhost:5200", "", "http://localhost:5200"),
    ("http://localhost:5200", "bad host!:1", "http://localhost:5200"),
    ("/relative", "10.0.0.5:5001", "/relative"),
])
def test_public_url_swaps_only_loopback_host_with_the_host_the_user_used(url, host, want):
    assert public_url(url, host) == want
