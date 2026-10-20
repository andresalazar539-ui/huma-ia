# ================================================================
# huma/tests/test_self_serve_link.py — "Próximo passo por link" (2026-10-19)
#
# Cobre:
#   - core/self_serve_link (puro): tudo "" sem link; UTM com canal;
#     parâmetros do dono vencem; defaults de rótulo e "quando";
#     detecção do link na resposta; card e entry de histórico
#   - build_dynamic_prompt inclui o bloco SÓ com link cadastrado
#   - build_web_identity: regra do site ganha a linha condicional só com link
#   - PATCH /settings aceita os campos (whitelist)
# ================================================================

from datetime import datetime

from huma.core import self_serve_link as ssl
from huma.core import web_channel
from huma.models.schemas import BusinessCategory, ClientIdentity, Conversation
from huma.services import ai_service


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_link",
        business_name="HUMA IA",
        business_description="Vendedora de IA pro WhatsApp.",
        category=BusinessCategory.SERVICOS,
        website="https://app.humaia.com.br/",
    )
    base.update(overrides)
    return ClientIdentity(**base)


def _conv(phone: str = "5511999998888") -> Conversation:
    return Conversation(
        client_id="cli_link",
        phone=phone,
        history=[{"role": "user", "content": "oi"}],
        last_message_at=datetime(2026, 10, 19, 12, 0, 0),
    )


class TestPureModule:
    def test_sem_link_tudo_vazio(self):
        ident = _identity()
        assert ssl.has_link(ident) is False
        assert ssl.link_for(ident, "site") == ""
        assert ssl.build_prompt(ident, "site") == ""
        assert ssl.web_rule_line(ident) == ""
        assert ssl.link_card(ident, "site") == {}
        assert ssl.reply_has_link("https://app.humaia.com.br/login", ident) is False

    def test_link_sem_http_nao_vale(self):
        assert ssl.has_link(_identity(self_serve_url="app.humaia.com.br/login")) is False
        assert ssl.has_link(_identity(self_serve_url="javascript:alert(1)")) is False

    def test_utm_por_canal_e_campanha_do_cliente(self):
        ident = _identity(self_serve_url="https://app.humaia.com.br/login")
        url = ssl.link_for(ident, "site")
        assert url.startswith("https://app.humaia.com.br/login?")
        assert "utm_source=huma" in url
        assert "utm_medium=site" in url
        assert "utm_campaign=huma_cli_link" in url

    def test_parametros_do_dono_vencem(self):
        ident = _identity(self_serve_url="https://x.com/go?utm_source=meu&ref=abc")
        url = ssl.link_for(ident, "whatsapp")
        assert "utm_source=meu" in url
        assert "ref=abc" in url
        assert "utm_medium=whatsapp" in url

    def test_defaults_de_rotulo_e_quando(self):
        ident = _identity(self_serve_url="https://x.com/go")
        assert ssl.label_for(ident) == ssl.DEFAULT_LABEL
        assert ssl.when_for(ident) == ssl.DEFAULT_WHEN
        ident2 = _identity(self_serve_url="https://x.com/go", self_serve_label="  Criar   conta ", self_serve_when="quiser testar")
        assert ssl.label_for(ident2) == "Criar conta"
        assert ssl.when_for(ident2) == "quiser testar"

    def test_prompt_e_condicional_e_carrega_o_link(self):
        ident = _identity(self_serve_url="https://app.humaia.com.br/login", self_serve_when="quiser testar")
        block = ssl.build_prompt(ident, "instagram")
        assert "PRÓXIMO PASSO POR LINK" in block
        assert "utm_medium=instagram" in block
        assert "QUANDO o lead quiser testar" in block
        assert "NÃO peça e-mail" in block
        assert "NÃO mande o link na abertura" in block
        assert "—" not in block

    def test_detecta_link_na_resposta_com_ou_sem_utm(self):
        ident = _identity(self_serve_url="https://app.humaia.com.br/login/")
        assert ssl.reply_has_link("Cria sua conta aqui: https://app.humaia.com.br/login?utm_source=huma", ident)
        assert ssl.reply_has_link("https://app.humaia.com.br/login", ident)
        assert ssl.reply_has_link("Te chamo no WhatsApp", ident) is False
        assert ssl.reply_has_link("", ident) is False

    def test_card_e_historico(self):
        ident = _identity(self_serve_url="https://x.com/go", self_serve_label="Testar grátis")
        card = ssl.link_card(ident, "site")
        assert card["kind"] == "link"
        assert card["title"] == "Testar grátis"
        assert card["url"].startswith("https://x.com/go?")
        assert card["buttons"][0]["title"] == "Testar grátis"
        entry = ssl.history_entry(card)
        assert entry["role"] == "assistant"
        assert entry["content"]  # nunca vazio (contrato do histórico)
        assert entry["cards"][0]["url"] == card["url"]
        assert entry["by"] == "ai"


class TestPromptWiring:
    def test_dinamico_sem_link_nao_tem_bloco(self):
        prompt = ai_service.build_dynamic_prompt(_identity(), _conv())
        assert "PRÓXIMO PASSO POR LINK" not in prompt

    def test_dinamico_com_link_tem_bloco_e_canal(self):
        ident = _identity(self_serve_url="https://app.humaia.com.br/login")
        prompt = ai_service.build_dynamic_prompt(ident, _conv("web:abc123abc123abc1"))
        assert "PRÓXIMO PASSO POR LINK" in prompt
        assert "utm_medium=site" in prompt
        prompt_ig = ai_service.build_dynamic_prompt(ident, _conv("ig:123"))
        assert "utm_medium=instagram" in prompt_ig

    def test_web_identity_regra_condicional(self):
        sem = web_channel.build_web_identity(_identity())
        assert "PRÓXIMO PASSO POR LINK" not in sem.custom_rules
        assert "peça o WhatsApp dele" in sem.custom_rules  # regra antiga intacta
        com = web_channel.build_web_identity(_identity(self_serve_url="https://x.com/go", self_serve_when="quiser testar"))
        assert "SE o lead quiser testar: mande o link do bloco PRÓXIMO PASSO POR LINK" in com.custom_rules
        assert "peça o WhatsApp dele" in com.custom_rules  # agendar/continuar depois segue por WhatsApp

    def test_whitelist_do_settings(self):
        from huma.routes.api import SETTINGS_EDITABLE_FIELDS
        for f in ("self_serve_url", "self_serve_label", "self_serve_when"):
            assert f in SETTINGS_EDITABLE_FIELDS
