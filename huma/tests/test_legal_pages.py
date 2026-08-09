# ================================================================
# huma/tests/test_legal_pages.py — Páginas legais públicas
#
# /privacidade e /termos: públicas (sem auth), HTML em pt-BR,
# com âncora de exclusão de dados exigida pelo app Meta.
# ================================================================


def _client():
    from fastapi.testclient import TestClient
    from huma.app import app
    return TestClient(app)


class TestLegalPages:

    def test_privacidade_publica_200(self):
        resp = _client().get("/privacidade")
        assert resp.status_code == 200
        assert "Política de Privacidade" in resp.text
        assert "HUMA IA" in resp.text

    def test_privacidade_tem_secao_exclusao(self):
        resp = _client().get("/privacidade")
        assert 'id="exclusao"' in resp.text
        assert "Exclusão de dados" in resp.text

    def test_termos_publico_200(self):
        resp = _client().get("/termos")
        assert resp.status_code == 200
        assert "Termos de Serviço" in resp.text
        assert "/privacidade" in resp.text
