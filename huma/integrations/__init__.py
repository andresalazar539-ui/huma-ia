"""
huma/integrations: registro único das CONEXÕES por token/chave (2026-11-06).

Até aqui cada integração nova ganhava colunas próprias em `clients`, uma rota
própria de connect, um modal próprio no Cockpit e um ramo próprio no
disconnect. Com ~25 integrações novas na fila (lista da ChatGuru), isso vira
retrabalho. A partir daqui:

- As credenciais de toda conexão nova moram em `clients.integrations` (JSONB,
  migration `scripts/migration_integrations_jsonb.sql`), no formato
  `{"<id>": {"<campo>": "...", "connected_at": iso, "label": "conta X", "meta": {...}}}`.
- Cada integração é um `IntegrationSpec` registrado por um módulo em
  `huma/integrations/specs/<id>.py` (descoberto sozinho por `load_specs()`).
  O módulo declara os campos que o dono cola, os passos no painel do provedor,
  o que a IA passa a fazer, o validador da credencial e a fábrica do provider.
- UMA rota genérica conecta (`POST /api/clients/{id}/connections/{iid}/connect`),
  o disconnect genérico limpa, `GET /api/integrations/catalog` alimenta o
  Cockpit (cards e modal genéricos) e as páginas públicas `/integracoes/<id>`.
- As integrações antigas (Nuvemshop, Bling, Omie, Tiny, Pipedrive, HubSpot, RD,
  Mercado Pago, Asaas, Google, Instagram) continuam nas colunas próprias.

Regras pra quem escreve um spec:
- `validate` NUNCA levanta: devolve {"status": "ok", "label": ..., "meta": {...}}
  ou {"status": "error", "user_message": "<frase em PT pro dono>"}.
- `meta` guarda só o que NÃO é segredo (nome da conta, funil, modelos de
  contrato, agendas). Segredo fica nos campos marcados `secret=True`.
- `effects` é a chave do `integration_effects.CONNECT_EFFECTS` ("crm",
  "scheduling", "store", "payment", "legal") ou "" quando conectar não liga
  capability nenhuma (assinatura, infoproduto).
- Nada aqui chama a IA.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from huma.utils.logger import get_logger

log = get_logger("integrations")

# Seções do Cockpit (mesmos ids/títulos do IntegrationsScreen.SECTIONS).
SECTION_LABELS: dict[str, str] = {
    "canais": "Canais de atendimento",
    "agenda": "Agenda",
    "loja": "Loja e estoque",
    "pagamentos": "Pagamentos",
    "cobranca": "Cobrança e financeiro",
    "crm": "CRM",
    "juridico": "Jurídico",
    "assinatura": "Assinatura digital",
    "marketing": "Marketing e infoprodutos",
    "anuncios": "Anúncios e automação",
    "assistentes": "Assistentes de IA",
}

# Tipo de provider que o spec entrega (decide onde o motor usa a conexão).
KINDS: tuple[str, ...] = (
    "crm",          # CRMProvider: lead qualificado vira negócio
    "inventory",    # InventoryProvider: catálogo, estoque, pedido
    "scheduling",   # SchedulingProvider: horários livres, agendar, cancelar
    "payment",      # link/cobrança de um valor pra fechar a venda
    "billing",      # BillingProvider: cobranças em aberto por CPF, segunda via
    "signature",    # SignatureProvider: contrato pra assinar a partir de modelo
    "legal",        # LegalProvider: CRM jurídico + consulta de processo
    "infoproduct",  # webhook de compra (Hotmart, Monetizze) vira contexto
    "automation",   # listagem/instrução (Zapier, Make, WordPress)
)


@dataclass(frozen=True)
class SpecField:
    """Um campo que o dono preenche no modal de conexão."""

    key: str
    label: str
    help: str = ""
    secret: bool = False
    placeholder: str = ""
    required: bool = True
    kind: str = "text"  # text | password | url | select
    options: tuple[tuple[str, str], ...] = ()  # (valor, rótulo) quando kind == select

    def public(self) -> dict:
        """Forma segura pro Cockpit e pras páginas públicas."""
        return {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "secret": self.secret,
            "placeholder": self.placeholder,
            "required": self.required,
            "kind": "password" if self.secret and self.kind == "text" else self.kind,
            "options": [list(o) for o in self.options],
        }


ValidateFn = Callable[[dict], Awaitable[dict]]
ConnectHook = Callable[[str, Any, dict, dict], Awaitable[dict]]
ProviderFactory = Callable[[Any], Any]


@dataclass
class IntegrationSpec:
    """Tudo que a HUMA precisa saber pra oferecer, conectar e usar uma integração."""

    id: str
    name: str
    section: str                      # chave de SECTION_LABELS
    kind: str                         # um de KINDS
    fields: list[SpecField]
    how_to: list[str]                 # passos no painel do provedor, em PT, pro dono
    what_ai_does: str                 # 1 parágrafo em PT: o que muda na conversa
    validate: ValidateFn              # confere a credencial; nunca levanta
    docs_url: str = ""
    site_url: str = ""
    effects: str = ""                 # chave de CONNECT_EFFECTS ou ""
    provider_factory: Optional[ProviderFactory] = None
    on_connect: Optional[ConnectHook] = None   # (client_id, identity, data, meta) -> extra
    # Webhook de entrada genérico: POST /webhook/connections/<id>/<client_id>.
    # `on_webhook(client_id, identity, payload, headers, query) -> dict` roda em
    # background e nunca levanta. `webhook_token=True` faz o connect gerar um
    # token (gravado em creds["webhook_token"]) que vai na URL (?token=) e é
    # conferido pela rota antes de chamar o spec; provedor com HMAC próprio
    # confere dentro do on_webhook.
    on_webhook: Optional[Callable[[str, Any, dict, dict, dict], Awaitable[dict]]] = None
    webhook_token: bool = False
    webhook_path: str = ""            # preenchido sozinho quando há on_webhook
    tags: tuple[str, ...] = ()        # verticais que mais usam (páginas públicas)
    faq: list[tuple[str, str]] = field(default_factory=list)  # (pergunta, resposta) pras páginas
    summary: str = ""                 # 1 frase pro card
    logo: str = ""                    # nome do arquivo em static/brand/integrations (vazio = sem logo oficial ainda)
    live_validated: bool = False      # True só depois de teste com conta real

    def public(self, base_url: str = "") -> dict:
        """Forma segura (sem funções) pro Cockpit e pras páginas públicas."""
        webhook = ""
        if self.webhook_path and base_url:
            webhook = base_url.rstrip("/") + self.webhook_path
        return {
            "id": self.id,
            "name": self.name,
            "section": self.section,
            "section_label": SECTION_LABELS.get(self.section, self.section),
            "kind": self.kind,
            "fields": [f.public() for f in self.fields],
            "how_to": list(self.how_to),
            "what_ai_does": self.what_ai_does,
            "docs_url": self.docs_url,
            "site_url": self.site_url,
            "webhook_url_template": webhook,
            "tags": list(self.tags),
            "faq": [list(q) for q in self.faq],
            "summary": self.summary,
            "logo": self.logo,
            "live_validated": self.live_validated,
        }


_REGISTRY: dict[str, IntegrationSpec] = {}
_LOADED = False


def register(spec: IntegrationSpec) -> IntegrationSpec:
    """Registra um spec (chamado no import de cada módulo em specs/)."""
    if spec.kind not in KINDS:
        raise ValueError(f"kind inválido em {spec.id}: {spec.kind}")
    if spec.section not in SECTION_LABELS:
        raise ValueError(f"section inválida em {spec.id}: {spec.section}")
    if spec.on_webhook is not None and not spec.webhook_path:
        spec.webhook_path = f"/webhook/connections/{spec.id}/{{client_id}}"
    _REGISTRY[spec.id] = spec
    return spec


def webhook_url_for(spec: IntegrationSpec, client_id: str, creds: dict, base_url: str) -> str:
    """URL de webhook desta conta (com token quando o spec exige), ou '' sem PUBLIC_BASE_URL."""
    if spec.on_webhook is None or not base_url:
        return ""
    url = base_url.rstrip("/") + spec.webhook_path.replace("{client_id}", client_id)
    token = str((creds or {}).get("webhook_token") or "")
    if spec.webhook_token and token:
        url += f"?token={token}"
    return url


def load_specs() -> dict[str, IntegrationSpec]:
    """Importa todos os módulos de huma/integrations/specs uma vez e devolve o registro."""
    global _LOADED
    if _LOADED:
        return _REGISTRY
    _LOADED = True
    try:
        from huma.integrations import specs as pkg
    except ImportError as e:
        log.warning(f"Integrações | pacote specs ausente | {e}")
        return _REGISTRY
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"huma.integrations.specs.{mod.name}")
        except Exception as e:  # um spec quebrado não derruba os outros
            log.error(f"Integrações | spec não carregou | {mod.name} | {type(e).__name__}: {e}")
    return _REGISTRY


def get_spec(integration_id: str) -> IntegrationSpec | None:
    """Spec pelo id (carrega o pacote na primeira chamada)."""
    return load_specs().get((integration_id or "").strip().lower())


def all_specs() -> list[IntegrationSpec]:
    """Todos os specs, ordenados por seção e nome."""
    specs = list(load_specs().values())
    order = list(SECTION_LABELS)
    return sorted(specs, key=lambda s: (order.index(s.section) if s.section in order else 99, s.name.lower()))


def specs_of_kind(kind: str) -> list[IntegrationSpec]:
    """Specs de um tipo (ex.: todos os CRMs por token)."""
    return [s for s in all_specs() if s.kind == kind]


# ----------------------------------------------------------------
# Leitura das credenciais no ClientIdentity
# ----------------------------------------------------------------

def creds_for(identity: Any, integration_id: str) -> dict:
    """Dict gravado em clients.integrations[<id>] ou {} quando não conectado."""
    store = getattr(identity, "integrations", None) or {}
    if not isinstance(store, dict):
        return {}
    data = store.get(integration_id) or {}
    return data if isinstance(data, dict) else {}


def is_connected(identity: Any, integration_id: str) -> bool:
    """Conectado = existe entrada com pelo menos um campo obrigatório preenchido."""
    data = creds_for(identity, integration_id)
    if not data:
        return False
    spec = get_spec(integration_id)
    if spec is None:
        return bool(data)
    required = [f.key for f in spec.fields if f.required]
    if not required:
        return bool(data.get("connected_at"))
    return all((data.get(k) or "") for k in required)


def connected_ids(identity: Any) -> list[str]:
    """Ids das conexões ativas deste cliente, na ordem do registro."""
    return [s.id for s in all_specs() if is_connected(identity, s.id)]


def first_connected_of_kind(identity: Any, kind: str) -> IntegrationSpec | None:
    """Primeiro spec conectado de um tipo (ex.: a agenda externa em uso)."""
    for spec in specs_of_kind(kind):
        if is_connected(identity, spec.id):
            return spec
    return None


def provider_for(identity: Any, integration_id: str) -> Any:
    """Instancia o provider do spec com as credenciais do cliente, ou None."""
    spec = get_spec(integration_id)
    if spec is None or spec.provider_factory is None or not is_connected(identity, integration_id):
        return None
    try:
        return spec.provider_factory(identity)
    except Exception as e:
        log.error(f"Integrações | provider não instanciou | {integration_id} | {type(e).__name__}: {e}")
        return None


def public_state(identity: Any, integration_id: str) -> dict:
    """Estado da conexão sem segredo (pro status do Cockpit)."""
    data = creds_for(identity, integration_id)
    spec = get_spec(integration_id)
    secret_keys = {f.key for f in (spec.fields if spec else [])}
    return {
        "connected": is_connected(identity, integration_id),
        "label": str(data.get("label") or ""),
        "connected_at": str(data.get("connected_at") or ""),
        "meta": {k: v for k, v in (data.get("meta") or {}).items() if k not in secret_keys}
        if isinstance(data.get("meta"), dict) else {},
    }


__all__ = [
    "IntegrationSpec", "SpecField", "SECTION_LABELS", "KINDS", "register", "load_specs",
    "get_spec", "all_specs", "specs_of_kind", "creds_for", "is_connected", "connected_ids",
    "first_connected_of_kind", "provider_for", "public_state", "webhook_url_for",
]
