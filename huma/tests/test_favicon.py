# ================================================================
# huma/tests/test_favicon.py — marca na aba do navegador (2026-09-05)
# ================================================================


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class TestFavicon:

    def test_favicon_ico_na_raiz(self):
        resp = _client().get("/favicon.ico")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert len(resp.content) > 100
        assert "max-age" in resp.headers.get("cache-control", "")

    def test_favicon_svg_e_apple(self):
        c = _client()
        svg = c.get("/favicon.svg")
        assert svg.status_code == 200 and b"<svg" in svg.content
        assert svg.headers["content-type"].startswith("image/svg+xml")
        apple = c.get("/apple-touch-icon.png")
        assert apple.status_code == 200 and apple.content[:4] == b"\x89PNG"

    def test_cockpit_e_login_linkam_o_icone(self):
        c = _client()
        assert 'rel="icon" href="/favicon.ico"' in c.get("/cockpit").text
        login = c.get("/login")
        assert login.status_code == 200
        assert 'href="/favicon.svg"' in login.text
