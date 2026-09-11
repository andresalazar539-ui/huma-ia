# ================================================================
# huma/core/permissions.py — Papéis da equipe e o que cada um pode fazer
#
# Módulo PURO (só stdlib). Fonte única de verdade dos papéis do Cockpit:
# o backend (middleware em app.py) e o frontend (window.HUMA_PERMS,
# injetado por routes/cockpit.py) leem daqui.
#
# Papéis (clients.team_members[].role; o dono da conta é sempre "dono"):
#   dono       — tudo, inclusive ajustes do negócio, integrações e equipe
#   admin      — Administrativo: relatórios, vendas, uso e faturamento,
#                disparos e divulgação, além de conversas/agenda/clientes
#   vendedor   — Vendas: conversas, agenda e clientes (recebe leads)
#   recepcao   — Recepção: conversas, agenda e clientes
#   equipe     — legado/sem papel definido = igual a recepção
#
# Permissões (áreas do Cockpit):
#   conversas   — Início, Conversas, Agenda, Clientes, assumir/responder
#   relatorios  — Relatórios, Vendas (placar), métricas, uso de IA
#   faturamento — Plano, créditos, indicação, pagamentos da assinatura
#   disparos    — Campanhas, mídia, links de divulgação
#   ajustes     — Negócio, Voz, funil, playbook, conhecimento, integrações,
#                 WhatsApp/Instagram, calendário, pixel/planilha
#   equipe      — convidar/editar/remover pessoas
#
# Rotas GET de "status" e o GET /settings ficam livres pra todo papel:
# as telas permitidas precisam deles pra desenhar (abas, nome do negócio).
# Bearer com api_key (integração do dono) nunca passa por aqui.
# ================================================================

from __future__ import annotations

import re
from typing import Any, Optional

OWNER_ROLE = "dono"

ALL_PERMISSIONS: tuple[str, ...] = (
    "conversas", "relatorios", "faturamento", "disparos", "ajustes", "equipe",
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "dono": frozenset(ALL_PERMISSIONS),
    "admin": frozenset({"conversas", "relatorios", "faturamento", "disparos"}),
    "vendedor": frozenset({"conversas"}),
    "recepcao": frozenset({"conversas"}),
    "equipe": frozenset({"conversas"}),
}

ROLE_LABELS: dict[str, str] = {
    "dono": "Dono",
    "vendedor": "Vendas",
    "recepcao": "Recepção",
    "admin": "Administrativo",
    "equipe": "Equipe",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "vendedor": "Conversas, agenda e clientes. Recebe os leads que a HUMA qualifica.",
    "recepcao": "Conversas, agenda e clientes.",
    "admin": "Conversas, relatórios, vendas, uso e faturamento, disparos e divulgação.",
    "dono": "Tudo, inclusive ajustes do negócio, integrações e equipe.",
}

# Telas do Cockpit → permissão exigida. Tela fora da lista = livre.
SCREEN_PERMISSIONS: dict[str, str] = {
    "inicio": "conversas",
    "conversas": "conversas",
    "agenda": "conversas",
    "clientes": "conversas",
    "vendas": "relatorios",
    "relatorios": "relatorios",
    "uso": "faturamento",
    "indicacao": "faturamento",
    "creditos": "faturamento",
    "planos": "faturamento",
    "checkout": "faturamento",
    "ajustes": "faturamento",
    "disparos": "disparos",
    "divulgacao": "disparos",
    "voz": "ajustes",
    "integracoes": "ajustes",
    "negocio": "ajustes",
}

# (métodos ou "*", regex do path, permissão). Primeira que casa vence.
# Ordem importa: os GETs livres vêm antes das regras largas.
_C = r"/api/clients/[^/]+"
_ROUTE_RULES: tuple[tuple[str, str, Optional[str]], ...] = (
    # Livres pra qualquer papel logado (as telas precisam pra desenhar)
    ("GET", rf"^{_C}/settings$", None),
    ("GET", rf"^{_C}/team$", None),
    ("GET", r"^/api/integrations/status$", None),
    ("GET", r"^/api/crm/status$", None),
    ("GET", r"^/whatsapp/(status|meta/status)$", None),
    ("GET", r"^/instagram/status$", None),
    # Equipe
    ("*", rf"^{_C}/team(/|$)", "equipe"),
    # Faturamento
    ("*", rf"^{_C}/(billing|referrals|payment)(/|$)", "faturamento"),
    ("GET", r"^/billing/spend-action$", "faturamento"),
    # Relatórios / métricas
    ("*", rf"^{_C}/(reports|metrics|ai-usage)(/|$)", "relatorios"),
    ("*", r"^/api/sales$", "relatorios"),
    # Disparos / divulgação
    ("*", rf"^{_C}/(outbound|media|tracking-link)(/|$)", "disparos"),
    # Ajustes do negócio e integrações
    ("*", rf"^{_C}/(settings|mode|funnel|playbook|knowledge|gaps|voice|calendar|"
          rf"pixel|sheet|asaas|analytics-ids|import-whatsapp)(/|$)", "ajustes"),
    ("*", r"^/api/integrations/", "ajustes"),
    ("*", r"^/whatsapp/", "ajustes"),
    ("*", r"^/instagram/", "ajustes"),
    ("*", r"^/oauth/", "ajustes"),
    # Conversas / agenda / clientes
    ("*", r"^/api/(conversations|appointments|customers|approve)(/|$)", "conversas"),
)
_COMPILED = tuple((m, re.compile(p), perm) for m, p, perm in _ROUTE_RULES)


def normalize_role(role: Any) -> str:
    """Papel conhecido em minúsculas; desconhecido/vazio vira 'equipe'."""
    r = str(role or "").strip().lower()
    return r if r in ROLE_PERMISSIONS else "equipe"


def role_for(client: Any, email: str) -> str:
    """
    Papel de quem está logado neste negócio.

    Sem e-mail (sessão antiga, Bearer, dev) ou e-mail do dono → 'dono'.
    E-mail da equipe → o papel cadastrado. E-mail desconhecido → 'equipe'
    (o acesso já foi decidido no login; aqui só se limita o que pode).
    """
    email = (email or "").strip().lower()
    if not email:
        return OWNER_ROLE
    owner = (getattr(client, "owner_email", "") or "").strip().lower()
    if owner and email == owner:
        return OWNER_ROLE
    for m in getattr(client, "team_members", None) or []:
        if isinstance(m, dict) and str(m.get("email") or "").strip().lower() == email:
            return normalize_role(m.get("role"))
    return "equipe"


def permissions_for(role: str) -> list[str]:
    """Lista ordenada das permissões do papel (pro frontend)."""
    perms = ROLE_PERMISSIONS.get(normalize_role(role), frozenset())
    return [p for p in ALL_PERMISSIONS if p in perms]


def can(role: str, permission: Optional[str]) -> bool:
    """True se o papel tem a permissão. Permissão None = livre."""
    if permission is None:
        return True
    return permission in ROLE_PERMISSIONS.get(normalize_role(role), frozenset())


def permission_for(method: str, path: str) -> Optional[str]:
    """Permissão exigida por uma rota; None = livre (ou rota fora do mapa)."""
    method = (method or "GET").upper()
    for m, rx, perm in _COMPILED:
        if (m == "*" or m == method) and rx.match(path or ""):
            return perm
    return None


def denied_message(role: str, permission: str) -> str:
    """Erro em português que ensina: qual papel e o que falta."""
    label = ROLE_LABELS.get(normalize_role(role), "Equipe")
    area = {
        "conversas": "às conversas",
        "relatorios": "aos relatórios e vendas",
        "faturamento": "ao plano e faturamento",
        "disparos": "aos disparos e divulgação",
        "ajustes": "aos ajustes do negócio e integrações",
        "equipe": "à gestão da equipe",
    }.get(permission, "a essa área")
    return f"Seu papel na equipe ({label}) não tem acesso {area}. Peça ao dono da conta."
