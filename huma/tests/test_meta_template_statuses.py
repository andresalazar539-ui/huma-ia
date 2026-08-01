# ================================================================
# Testes da Fase 0 do Escudo antiban:
#   - Template REAL da Meta (type=template via Graph API)
#   - parse_meta_statuses (telemetria de entrega/erro)
#   - Ingestão de statuses (contadores + opt-out 131050)
#   - Outbound com template + supressão de opt-out
# ================================================================

import asyncio

from huma.services import whatsapp_service as wa


class _MetaIdentity:
    client_id = "cli_meta"
    whatsapp_provider = "meta"
    phone_number_id = "PNID123"
    meta_access_token = "tok_meta"


# ── _meta_send_template: payload da Graph API ──

class TestMetaSendTemplate:
    def _capture_post(self, monkeypatch):
        captured = {}

        async def fake_post(url, headers, body):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = body
            return "wamid.TEMPLATE1"

        monkeypatch.setattr(wa, "_meta_post_raw", fake_post)
        return captured

    def test_payload_com_params(self, monkeypatch):
        captured = self._capture_post(monkeypatch)
        mid = asyncio.run(wa._meta_send_template(
            _MetaIdentity(), "+55 11 99999-8888", "promo_julho", "pt_BR", ["André", "10%"]
        ))
        assert mid == "wamid.TEMPLATE1"
        body = captured["body"]
        assert body["type"] == "template"
        assert body["to"] == "5511999998888"
        assert body["template"]["name"] == "promo_julho"
        assert body["template"]["language"] == {"code": "pt_BR"}
        params = body["template"]["components"][0]["parameters"]
        assert body["template"]["components"][0]["type"] == "body"
        assert [p["text"] for p in params] == ["André", "10%"]
        assert all(p["type"] == "text" for p in params)

    def test_payload_sem_params_nao_tem_components(self, monkeypatch):
        captured = self._capture_post(monkeypatch)
        asyncio.run(wa._meta_send_template(
            _MetaIdentity(), "5511999998888", "aviso_simples", "pt_BR", []
        ))
        assert "components" not in captured["body"]["template"]

    def test_sem_credenciais_retorna_none(self):
        class _SemCreds:
            client_id = "cli_x"
            phone_number_id = ""
            meta_access_token = ""

        mid = asyncio.run(wa._meta_send_template(_SemCreds(), "5511999998888", "t", "pt_BR", []))
        assert mid is None


# ── send_template: roteamento por canal ──

class TestSendTemplateRouting:
    def test_meta_envia_template_real(self, monkeypatch):
        captured = {}

        async def fake_resolve(client_id):
            return "meta", _MetaIdentity()

        async def fake_template(identity, phone, name, language, params):
            captured["name"] = name
            captured["language"] = language
            captured["params"] = params
            return "wamid.X"

        monkeypatch.setattr(wa, "_resolve_channel", fake_resolve)
        monkeypatch.setattr(wa, "_meta_send_template", fake_template)

        mid = asyncio.run(wa.send_template(
            "5511999998888", "promo", ["Zé"], client_id="cli_meta", language="pt_BR"
        ))
        assert mid == "wamid.X"
        assert captured["name"] == "promo"
        assert captured["language"] == "pt_BR"
        assert captured["params"] == ["Zé"]

    def test_fora_do_meta_degrada_pra_texto(self, monkeypatch):
        captured = {}

        async def fake_resolve(client_id):
            return "twilio", None

        async def fake_text(phone, text, client_id="", **kwargs):
            captured["text"] = text
            return "SID1"

        monkeypatch.setattr(wa, "_resolve_channel", fake_resolve)
        monkeypatch.setattr(wa, "send_text", fake_text)

        mid = asyncio.run(wa.send_template("5511999998888", "promo", ["Zé"], client_id="cli_tw"))
        assert mid == "SID1"
        assert captured["text"].startswith("[Template: promo]")


# ── parse_meta_statuses ──

class TestParseMetaStatuses:
    def _envelope(self, status: dict, pnid="PNID123"):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "551130000000", "phone_number_id": pnid},
                                "statuses": [status],
                            },
                        }
                    ],
                }
            ],
        }

    def test_delivered(self):
        sts = wa.parse_meta_statuses(self._envelope(
            {"id": "wamid.1", "status": "delivered", "timestamp": "1722400000", "recipient_id": "5511999998888"}
        ))
        assert len(sts) == 1
        st = sts[0]
        assert st["phone_number_id"] == "PNID123"
        assert st["phone"] == "5511999998888"
        assert st["message_id"] == "wamid.1"
        assert st["status"] == "delivered"
        assert st["error_code"] == 0
        assert st["error_title"] == ""

    def test_failed_com_erro_131050(self):
        sts = wa.parse_meta_statuses(self._envelope({
            "id": "wamid.2", "status": "failed", "recipient_id": "5511999997777",
            "errors": [{"code": 131050, "title": "User preferences to stop marketing messages"}],
        }))
        assert sts[0]["status"] == "failed"
        assert sts[0]["error_code"] == 131050
        assert "marketing" in sts[0]["error_title"].lower()

    def test_status_nao_polui_parse_de_mensagens(self):
        env = self._envelope({"id": "w", "status": "read", "recipient_id": "551199"})
        assert wa.parse_meta_webhook(env) == []
        assert len(wa.parse_meta_statuses(env)) == 1

    def test_mensagem_nao_gera_status(self):
        env = {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "PNID123"},
                "messages": [{"from": "551199", "id": "w1", "type": "text", "text": {"body": "oi"}}],
            }}]}],
        }
        assert wa.parse_meta_statuses(env) == []
        assert len(wa.parse_meta_webhook(env)) == 1

    def test_body_invalido(self):
        assert wa.parse_meta_statuses(None) == []
        assert wa.parse_meta_statuses({"object": "page"}) == []
        assert wa.parse_meta_statuses({"object": "whatsapp_business_account", "entry": ["x"]}) == []

    def test_codigo_de_erro_nao_numerico_vira_zero(self):
        sts = wa.parse_meta_statuses(self._envelope({
            "id": "w", "status": "failed", "recipient_id": "551199",
            "errors": [{"code": "abc", "title": "estranho"}],
        }))
        assert sts[0]["error_code"] == 0
        assert sts[0]["error_title"] == "estranho"


# ── _ingest_meta_statuses (telemetria + opt-out) ──

class TestIngestMetaStatuses:
    def _setup(self, monkeypatch, client_found=True):
        import huma.routes.api as api

        incremented = []
        stored = {}

        class _Client:
            client_id = "cli_meta"

        async def fake_get_client(pnid):
            return _Client() if client_found else None

        async def fake_incr(key, ttl):
            incremented.append(key)
            return 1

        async def fake_set(key, value, ttl):
            stored[key] = value

        monkeypatch.setattr(api.db, "get_client_by_phone_number_id", fake_get_client)
        monkeypatch.setattr(api.cache, "incr_with_ttl", fake_incr)
        monkeypatch.setattr(api.cache, "set_with_ttl", fake_set)
        return api, incremented, stored

    def test_delivered_so_conta(self, monkeypatch):
        api, incremented, stored = self._setup(monkeypatch)
        asyncio.run(api._ingest_meta_statuses([
            {"phone_number_id": "PNID123", "phone": "551199", "message_id": "w",
             "status": "delivered", "timestamp": "", "error_code": 0, "error_title": ""},
        ]))
        assert len(incremented) == 1
        assert incremented[0].startswith("wastatus:cli_meta:delivered:")
        assert stored == {}

    def test_131050_marca_optout(self, monkeypatch):
        api, incremented, stored = self._setup(monkeypatch)
        asyncio.run(api._ingest_meta_statuses([
            {"phone_number_id": "PNID123", "phone": "5511999997777", "message_id": "w",
             "status": "failed", "timestamp": "", "error_code": 131050, "error_title": "stop"},
        ]))
        assert "optout:cli_meta:5511999997777" in stored
        assert any(":err131050:" in k for k in incremented)

    def test_cliente_desconhecido_nao_quebra(self, monkeypatch):
        api, incremented, stored = self._setup(monkeypatch, client_found=False)
        asyncio.run(api._ingest_meta_statuses([
            {"phone_number_id": "PNID_X", "phone": "551199", "message_id": "w",
             "status": "failed", "timestamp": "", "error_code": 131049, "error_title": ""},
        ]))
        assert incremented == []
        assert stored == {}

    def test_redis_fora_nao_quebra(self, monkeypatch):
        import huma.routes.api as api

        class _Client:
            client_id = "cli_meta"

        async def fake_get_client(pnid):
            return _Client()

        async def boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr(api.db, "get_client_by_phone_number_id", fake_get_client)
        monkeypatch.setattr(api.cache, "incr_with_ttl", boom)
        monkeypatch.setattr(api.cache, "set_with_ttl", boom)

        # Não pode levantar — telemetria é silent-fail
        asyncio.run(api._ingest_meta_statuses([
            {"phone_number_id": "PNID123", "phone": "551199", "message_id": "w",
             "status": "failed", "timestamp": "", "error_code": 131050, "error_title": ""},
        ]))


# ── process_outbound_campaign: template real + opt-out ──

class TestOutboundTemplateFlow:
    def _setup(self, monkeypatch, optout=False, send_result="wamid.OK"):
        import huma.core.orchestrator as orch

        calls = {"template": [], "text": [], "debits": 0}

        async def fake_exists(key):
            return optout

        async def fake_credits(client_id):
            return {"has_conversations": True, "balance": 100}

        async def fake_debit(client_id):
            calls["debits"] += 1
            return True

        async def fake_send_template(phone, name, params, client_id="", language="pt_BR", **kw):
            calls["template"].append({"phone": phone, "name": name, "params": params, "language": language})
            return send_result

        async def fake_send_text(phone, text, client_id="", **kw):
            calls["text"].append({"phone": phone, "text": text})
            return send_result

        async def fake_outbound_msg(client, lead, template):
            return "oi, tudo bem?"

        async def fake_sleep(_):
            return None

        monkeypatch.setattr(orch.cache, "exists", fake_exists)
        monkeypatch.setattr(orch.billing, "check_conversations", fake_credits)
        monkeypatch.setattr(orch.billing, "debit_conversation", fake_debit)
        monkeypatch.setattr(orch.wa, "send_template", fake_send_template)
        monkeypatch.setattr(orch.wa, "send_text", fake_send_text)
        monkeypatch.setattr(orch.ai, "generate_outbound_message", fake_outbound_msg)
        monkeypatch.setattr(orch.asyncio, "sleep", fake_sleep)
        return orch, calls

    def _campaign(self, **overrides):
        from huma.models.schemas import OutboundCampaign, OutboundLead

        data = {
            "campaign_id": "camp_1",
            "client_id": "cli_meta",
            "name": "Promo",
            "message_template": "oferta de julho",
            "leads": [OutboundLead(phone="5511999998888", name="Zé")],
        }
        data.update(overrides)
        return OutboundCampaign(**data)

    class _Meta:
        client_id = "cli_meta"
        whatsapp_provider = "meta"

    def test_template_name_envia_template_real(self, monkeypatch):
        orch, calls = self._setup(monkeypatch)
        campaign = self._campaign(
            template_name="promo_julho",
            template_language="pt_BR",
            template_params=["{nome}", "10%"],
        )
        result = asyncio.run(orch.process_outbound_campaign(self._Meta(), campaign))
        assert result["sent"] == 1
        assert len(calls["template"]) == 1
        sent = calls["template"][0]
        assert sent["name"] == "promo_julho"
        assert sent["params"] == ["Zé", "10%"]  # {nome} substituído
        assert sent["language"] == "pt_BR"
        assert calls["text"] == []
        assert calls["debits"] == 1

    def test_sem_template_name_mantem_texto_livre(self, monkeypatch):
        orch, calls = self._setup(monkeypatch)
        result = asyncio.run(orch.process_outbound_campaign(self._Meta(), self._campaign()))
        assert result["sent"] == 1
        assert calls["template"] == []
        assert len(calls["text"]) == 1

    def test_optout_suprime_lead(self, monkeypatch):
        from huma.models.schemas import OutboundStatus

        orch, calls = self._setup(monkeypatch, optout=True)
        campaign = self._campaign(template_name="promo_julho")
        result = asyncio.run(orch.process_outbound_campaign(self._Meta(), campaign))
        assert result["sent"] == 0
        assert result["skipped"] == 1
        assert calls["template"] == []
        assert calls["text"] == []
        assert calls["debits"] == 0
        assert campaign.leads[0].status == OutboundStatus.STOPPED

    def test_envio_falho_nao_debita_e_mantem_pending(self, monkeypatch):
        from huma.models.schemas import OutboundStatus

        orch, calls = self._setup(monkeypatch, send_result=None)
        campaign = self._campaign(template_name="promo_julho")
        result = asyncio.run(orch.process_outbound_campaign(self._Meta(), campaign))
        assert result["sent"] == 0
        assert result["errors"] == 1
        assert calls["debits"] == 0
        assert campaign.leads[0].status == OutboundStatus.PENDING
