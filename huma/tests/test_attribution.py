# ================================================================
# huma/tests/test_attribution.py — Atribuição de origem do lead
#
# Cobre:
#   - resolve_source: referral CTWA > código #h > utm no texto
#   - normalize_utm_source: aliases + medium pago (fb/ig → meta_ads)
#   - has_signal: gate barato (sem falso positivo em conversa normal)
#   - build_tracking_link: código, texto e link wa.me
#   - Parsers de webhook: referral da Meta e externalAdReply (Evolution)
#   - capture: first-touch via db.set_lead_source, silencioso sem sinal
#   - Relatório: seção "origem" (maior origem de conversas e conversões)
# ================================================================

import asyncio

from huma.services import attribution_service as attr
from huma.services import whatsapp_service as wa


# ================================================================
# resolve_source / normalize
# ================================================================

class TestResolveSource:

    def test_referral_ctwa_vira_meta_ads(self):
        referral = {
            "source_type": "ad",
            "source_id": "1234567890",
            "source_url": "https://fb.me/abc",
            "headline": "Promoção de julho",
            "ctwa_clid": "clid-xyz",
        }
        source, detail, ref = attr.resolve_source(referral, "Olá, vi o anúncio")
        assert source == "meta_ads"
        assert detail == "Promoção de julho"
        assert ref == "clid-xyz"

    def test_referral_post_organico_vira_facebook(self):
        referral = {"source_type": "post", "source_id": "99", "headline": ""}
        source, _, _ = attr.resolve_source(referral, "")
        assert source == "facebook"

    def test_codigo_rastreavel_google_ads(self):
        source, detail, ref = attr.resolve_source(None, "Olá! Quero saber mais. #hga-promo-junho")
        assert source == "google_ads"
        assert detail == "promo-junho"
        assert ref == "#hga-promo-junho"

    def test_codigo_rastreavel_linkedin_sem_campanha(self):
        source, detail, _ = attr.resolve_source(None, "Oi, vim pelo link #hli")
        assert source == "linkedin"
        assert detail == ""

    def test_utm_source_no_texto(self):
        text = "vi isso aqui https://site.com/?utm_source=google&utm_medium=cpc&utm_campaign=inverno"
        source, detail, _ = attr.resolve_source(None, text)
        assert source == "google_ads"
        assert detail == "inverno"

    def test_utm_instagram_pago_vira_meta_ads(self):
        text = "https://x.com/?utm_source=instagram&utm_medium=paid"
        source, _, _ = attr.resolve_source(None, text)
        assert source == "meta_ads"

    def test_sem_sinal_retorna_vazio(self):
        assert attr.resolve_source(None, "Oi, quanto custa a consulta?") == ("", "", "")

    def test_referral_tem_prioridade_sobre_codigo(self):
        referral = {"source_type": "ad", "ctwa_clid": "c1"}
        source, _, _ = attr.resolve_source(referral, "mensagem com #hga")
        assert source == "meta_ads"


class TestNormalizeUtm:

    def test_aliases_google(self):
        for raw in ("google", "adwords", "gads", "googleads"):
            assert attr.normalize_utm_source(raw) == "google_ads"

    def test_facebook_organico_fica_facebook(self):
        assert attr.normalize_utm_source("facebook", "social") == "facebook"

    def test_fonte_desconhecida_sanitizada(self):
        assert attr.normalize_utm_source("Meu Portal!") == "meu_portal"

    def test_vazio(self):
        assert attr.normalize_utm_source("") == ""

    def test_ia_chatgpt_gemini(self):
        for raw in ("chatgpt", "chatgpt.com", "gemini", "perplexity", "openai"):
            assert attr.normalize_utm_source(raw) == "ia"

    def test_google_organico_vira_busca_organica(self):
        assert attr.normalize_utm_source("google", "organic") == "busca_organica"
        assert attr.normalize_utm_source("google", "cpc") == "google_ads"


class TestHasSignal:

    def test_conversa_normal_nao_dispara(self):
        assert attr.has_signal(None, "Oi, queria agendar um horário #hoje pode?") is False

    def test_referral_dispara(self):
        assert attr.has_signal({"ctwa_clid": "x"}, "") is True

    def test_codigo_dispara(self):
        assert attr.has_signal(None, "Olá! #hga-promo") is True

    def test_utm_dispara(self):
        assert attr.has_signal(None, "https://a.b/?utm_source=linkedin") is True


# ================================================================
# build_tracking_link
# ================================================================

class TestTrackingLink:

    def test_link_completo(self):
        r = attr.build_tracking_link("google_ads", campaign="Promo Junho", phone="+55 11 99999-9999")
        assert r["source"] == "google_ads"
        assert r["code"] == "#hga-promo-junho"
        assert r["text"].endswith("#hga-promo-junho")
        assert r["link"].startswith("https://wa.me/5511999999999?text=")
        assert "%23hga-promo-junho" in r["link"]  # código URL-encoded

    def test_sem_phone_retorna_so_texto(self):
        r = attr.build_tracking_link("linkedin")
        assert r["code"] == "#hli"
        assert r["link"] == ""

    def test_source_invalida_levanta_valueerror(self):
        import pytest
        with pytest.raises(ValueError):
            attr.build_tracking_link("radio_am")

    def test_roundtrip_codigo_gerado_e_detectado(self):
        r = attr.build_tracking_link("tiktok_ads", campaign="lancamento")
        source, detail, _ = attr.resolve_source(None, r["text"])
        assert source == "tiktok_ads"
        assert detail == "lancamento"


# ================================================================
# Parsers de webhook (referral)
# ================================================================

class TestWebhookReferral:

    def test_meta_webhook_extrai_referral(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "PNID1"},
                "contacts": [{"profile": {"name": "Lead"}}],
                "messages": [{
                    "from": "5511988887777",
                    "id": "wamid.1",
                    "type": "text",
                    "text": {"body": "Olá! Vi o anúncio"},
                    "referral": {
                        "source_url": "https://fb.me/xyz",
                        "source_id": "AD123",
                        "source_type": "ad",
                        "headline": "Compre agora",
                        "ctwa_clid": "clid-1",
                    },
                }],
            }}]}],
        }
        msgs = wa.parse_meta_webhook(body)
        assert len(msgs) == 1
        assert msgs[0]["referral"]["ctwa_clid"] == "clid-1"
        assert msgs[0]["referral"]["source_type"] == "ad"

    def test_meta_webhook_sem_referral_vem_dict_vazio(self):
        body = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "PNID1"},
                "messages": [{"from": "551188", "id": "w1", "type": "text",
                              "text": {"body": "oi"}}],
            }}]}],
        }
        msgs = wa.parse_meta_webhook(body)
        assert msgs[0]["referral"] == {}

    def test_evolution_webhook_normaliza_external_ad_reply(self):
        body = {
            "instance": "inst1",
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "5511977776666@s.whatsapp.net", "fromMe": False, "id": "m1"},
                "pushName": "Lead",
                "message": {
                    "extendedTextMessage": {
                        "text": "Olá! Vi o anúncio",
                        "contextInfo": {
                            "externalAdReply": {
                                "title": "Oferta relâmpago",
                                "body": "50% off",
                                "sourceUrl": "https://fb.me/abc",
                                "sourceId": "AD9",
                                "sourceType": "ad",
                                "ctwaClid": "clid-evo",
                            },
                        },
                    },
                },
            },
        }
        parsed = wa.parse_evolution_webhook(body)
        assert parsed is not None
        ref = parsed["referral"]
        assert ref["ctwa_clid"] == "clid-evo"
        assert ref["headline"] == "Oferta relâmpago"
        # shape normalizado = mesmo resolve dos dois canais
        source, detail, _ = attr.resolve_source(ref, parsed["text"])
        assert source == "meta_ads"
        assert detail == "Oferta relâmpago"

    def test_evolution_webhook_sem_anuncio_referral_vazio(self):
        body = {
            "instance": "inst1",
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "551197777@s.whatsapp.net", "fromMe": False, "id": "m2"},
                "message": {"conversation": "oi, tudo bem?"},
            },
        }
        parsed = wa.parse_evolution_webhook(body)
        assert parsed["referral"] == {}


# ================================================================
# capture (first-touch, sem exception)
# ================================================================

class TestCapture:

    def test_capture_grava_origem_resolvida(self, monkeypatch):
        from huma.services import db_service as db
        chamadas = []

        async def fake_set(client_id, phone, source, detail="", ref=""):
            chamadas.append((client_id, phone, source, detail, ref))
            return True

        monkeypatch.setattr(db, "set_lead_source", fake_set)
        asyncio.run(attr.capture("cli1", "5511988", {"source_type": "ad", "ctwa_clid": "c9"}, "oi"))
        assert chamadas == [("cli1", "5511988", "meta_ads", "", "c9")]

    def test_capture_sem_sinal_nao_toca_banco(self, monkeypatch):
        from huma.services import db_service as db

        async def explode(*a, **kw):
            raise AssertionError("não devia tocar o banco")

        monkeypatch.setattr(db, "set_lead_source", explode)
        asyncio.run(attr.capture("cli1", "5511988", None, "oi, quanto custa?"))

    def test_capture_erro_de_banco_nao_propaga(self, monkeypatch):
        from huma.services import db_service as db

        async def falha(*a, **kw):
            raise RuntimeError("supabase off")

        monkeypatch.setattr(db, "set_lead_source", falha)
        # não pode levantar — atribuição jamais derruba o fluxo
        asyncio.run(attr.capture("cli1", "5511988", None, "#hga-promo"))


# ================================================================
# Relatório: seção "origem"
# ================================================================

class TestReportOrigem:

    def _report(self, monkeypatch, convs):
        from huma.models.schemas import ClientIdentity
        from huma.services import report_service as rs
        from huma.tests.test_reports import _fake_supa

        _fake_supa(monkeypatch, conversations=convs)
        identity = ClientIdentity(client_id="cli_o", business_name="Loja", capabilities=[])
        return asyncio.run(rs.build_report(identity, days=7))

    def test_maior_origem_de_conversas_e_conversoes(self, monkeypatch):
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        convs = (
            [{"phone": str(i), "stage": "discovery", "created_at": now,
              "last_message_at": now, "lead_source": "meta_ads"} for i in range(3)]
            + [{"phone": "w1", "stage": "won", "created_at": now,
                "last_message_at": now, "lead_source": "google_ads"}]
            + [{"phone": "o1", "stage": "offer", "created_at": now,
                "last_message_at": now, "lead_source": ""}]
        )
        report = self._report(monkeypatch, convs)
        origem = report["sections"]["origem"]

        assert origem["top_conversas"] == "Meta Ads (3)"
        assert origem["top_conversoes"] == "Google Ads (1)"
        slugs = {f["slug"]: f for f in origem["fontes"]}
        assert slugs["meta_ads"]["conversas"] == 3
        assert slugs["meta_ads"]["categoria"] == "pago"
        assert slugs["google_ads"]["ganhos"] == 1
        assert slugs["organico"]["conversas"] == 1
        assert slugs["organico"]["categoria"] == "organico"

    def test_agendamentos_e_receita_por_fonte(self, monkeypatch):
        from datetime import datetime
        from huma.models.schemas import ClientIdentity
        from huma.services import report_service as rs
        from huma.tests.test_reports import _fake_supa

        now = datetime.utcnow().isoformat()
        convs = [
            {"phone": "111", "stage": "won", "created_at": now, "last_message_at": now,
             "lead_source": "meta_ads", "active_appointment_datetime": now},
            {"phone": "222", "stage": "offer", "created_at": now, "last_message_at": now,
             "lead_source": "meta_ads"},
            {"phone": "333", "stage": "won", "created_at": now, "last_message_at": now,
             "lead_source": "google_ads"},
        ]
        payments = [
            {"amount_cents": 50000, "paid_at": now, "status": "approved", "phone": "111"},
            {"amount_cents": 30000, "paid_at": now, "status": "approved", "phone": "333"},
        ]
        _fake_supa(monkeypatch, conversations=convs, payments=payments)
        identity = ClientIdentity(client_id="cli_o", business_name="Loja",
                                  capabilities=["sell_digital"])
        report = asyncio.run(rs.build_report(identity, days=7))

        slugs = {f["slug"]: f for f in report["sections"]["origem"]["fontes"]}
        assert slugs["meta_ads"]["agendamentos"] == 1
        assert slugs["meta_ads"]["receita_cents"] == 50000
        assert slugs["meta_ads"]["share_pct"] == 67
        assert slugs["google_ads"]["receita_cents"] == 30000
        assert slugs["google_ads"]["receita_display"] == "R$ 300,00"

    def test_outbound_conta_como_disparo(self, monkeypatch):
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        convs = [{"phone": "1", "stage": "offer", "created_at": now,
                  "last_message_at": now, "lead_source": "", "is_outbound": True}]
        report = self._report(monkeypatch, convs)
        fontes = report["sections"]["origem"]["fontes"]
        assert fontes[0]["slug"] == "outbound"

    def test_whatsapp_mostra_origem_so_com_lead_atribuido(self, monkeypatch):
        from datetime import datetime
        from huma.models.schemas import ClientIdentity
        from huma.services import report_service as rs

        now = datetime.utcnow().isoformat()
        identity = ClientIdentity(client_id="cli_o", business_name="Loja", capabilities=[])

        report = self._report(monkeypatch, [
            {"phone": "1", "stage": "won", "created_at": now,
             "last_message_at": now, "lead_source": "meta_ads"},
        ])
        msg = rs.format_report_whatsapp(identity, report)
        assert "Maior origem de conversas" in msg
        assert "Maior origem de conversões" in msg

        report_organico = self._report(monkeypatch, [
            {"phone": "1", "stage": "offer", "created_at": now,
             "last_message_at": now, "lead_source": ""},
        ])
        msg2 = rs.format_report_whatsapp(identity, report_organico)
        assert "Maior origem" not in msg2
