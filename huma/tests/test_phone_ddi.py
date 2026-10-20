# ================================================================
# huma/tests/test_phone_ddi.py — número sem o DDI 55 (2026-10-19)
#
# Incidente: o dono cadastrou o próprio WhatsApp ("11984023038") e o
# destinatário do relatório sem o 55; o Evolution devolveu
# `exists: false` pra todo aviso, follow-up de teste e relatório.
#
# Cobre:
#   - wa.br_e164 (puro): BR sem DDI ganha 55; com DDI, sintético e
#     estrangeiro ficam como estão
#   - destino do Evolution e do Meta sai com DDI
#   - PATCH /settings grava owner_phone e report_recipients com o 55
# ================================================================

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from huma.app import app
from huma.models.schemas import BusinessCategory, ClientIdentity
from huma.services import whatsapp_service as wa


class TestBrE164:
    def test_celular_sem_ddi_ganha_55(self):
        assert wa.br_e164("11984023038") == "5511984023038"
        assert wa.br_e164("(11) 98402-3038") == "5511984023038"
        assert wa.br_e164("+55 11 98402-3038") == "5511984023038"

    def test_fixo_sem_ddi_ganha_55(self):
        assert wa.br_e164("1133334444") == "551133334444"

    def test_com_ddi_fica_igual(self):
        assert wa.br_e164("5511984023038") == "5511984023038"
        assert wa.br_e164("5511984023038@s.whatsapp.net") == "5511984023038"

    def test_sintetico_e_estrangeiro_ficam_iguais(self):
        assert wa.br_e164("ig:123") == "ig:123"
        assert wa.br_e164("web:abc") == "web:abc"
        assert wa.br_e164("12125551234") == "12125551234"  # EUA sem o 1: 3º dígito não é 9
        assert wa.br_e164("4915112345678") == "4915112345678"
        assert wa.br_e164("") == ""

    def test_ddd_invalido_nao_ganha_55(self):
        assert wa.br_e164("10984023038") == "10984023038"


class TestDestinos:
    def test_evolution_destino_com_ddi(self):
        ident = ClientIdentity(client_id="cli_ddi", business_name="X", evolution_instance="cli_ddi")
        with patch("huma.services.redis_service.get_value", new=AsyncMock(return_value=None)):
            dest = asyncio.run(wa._evo_destination(ident, "11984023038"))
        assert dest == "5511984023038"

    def test_meta_to_com_ddi(self):
        ident = ClientIdentity(
            client_id="cli_ddi", business_name="X", whatsapp_provider="meta",
            phone_number_id="123", meta_access_token="tok",
        )
        captured = {}

        async def fake_meta_send(identity, body):
            captured.update(body)
            return "wamid.1"

        with patch.object(wa, "_meta_send", new=fake_meta_send):
            asyncio.run(wa._meta_send_text(ident, "11984023038", "oi", None))
        assert captured.get("to") == "5511984023038"


class TestSettingsNormaliza:
    def test_patch_grava_owner_phone_e_recipients_com_55(self, monkeypatch):
        import huma.core.auth as auth_mod
        import huma.routes.api as api_mod

        ident = ClientIdentity(client_id="cli_ddi", business_name="X", category=BusinessCategory.SERVICOS)
        saved = {}

        async def get_client(cid):
            return ident if cid == "cli_ddi" else None

        async def fake_update(client_id, updates):
            saved.update(updates)

        monkeypatch.setattr(api_mod.db, "get_client", get_client)
        monkeypatch.setattr(api_mod.db, "update_client", fake_update)
        monkeypatch.setattr(auth_mod, "get_client", get_client)
        monkeypatch.setattr(auth_mod, "SESSION_SECRET", "segredo-teste")
        cookies = {"huma_session": auth_mod.create_session_token("cli_ddi")}

        r = TestClient(app).patch(
            "/api/clients/cli_ddi/settings",
            json={"owner_phone": "11984023038", "report_recipients": ["11995232507", "a@b.com"]},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert saved["owner_phone"] == "5511984023038"
        assert saved["report_recipients"] == ["5511995232507", "a@b.com"]
