# ================================================================
# huma/routes/wizard.py — Endpoints REST do wizard self-service (Fase 4)
#
# Frontend (HTML inline aqui ou dashboard real depois) consome:
#   GET    /wizard/{client_id}/state           — estado completo
#   POST   /wizard/{client_id}/vertical        — escolhe categoria
#   POST   /wizard/{client_id}/capabilities    — ativa/desativa caps
#   POST   /wizard/{client_id}/activate        — ativa o clone (status→ACTIVE)
#   GET    /wizard/                            — página HTML inicial
#
# Princípio: validação roda no backend antes de qualquer mutação.
# Combinação inválida vira HTTP 400 com mensagem clara — o frontend
# nunca consegue ativar SELL_PHYSICAL sem Bling, mesmo via curl.
# ================================================================

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from huma.core.capabilities import Capability
from huma.models.schemas import BusinessCategory, OnboardingStatus
from huma.onboarding import wizard
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("wizard_routes")
router = APIRouter(prefix="/wizard", tags=["Onboarding Wizard"])


# ================================================================
# PAYLOADS
# ================================================================


class VerticalSelection(BaseModel):
    vertical: str = Field(..., min_length=1, description="Slug da BusinessCategory")


class CapabilitiesSelection(BaseModel):
    capabilities: list[str] = Field(
        default_factory=list,
        description="Lista de slugs de Capability a ativar",
    )


# ================================================================
# HELPERS
# ================================================================


def _parse_vertical(slug: str) -> BusinessCategory:
    try:
        return BusinessCategory(slug)
    except ValueError:
        valid = ", ".join(c.value for c in BusinessCategory)
        raise HTTPException(
            400, f"Vertical inválida: '{slug}'. Válidas: {valid}",
        )


def _parse_capability(slug: str) -> Capability:
    try:
        return Capability(slug)
    except ValueError:
        valid = ", ".join(c.value for c in Capability)
        raise HTTPException(
            400, f"Capability inválida: '{slug}'. Válidas: {valid}",
        )


async def _get_identity_or_404(client_id: str):
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    return identity


# ================================================================
# GET /wizard/{client_id}/state
# ================================================================


@router.get("/{client_id}/state")
async def get_state(client_id: str):
    """
    Estado completo do wizard pra esse cliente.

    Retorna:
      - business_name + category (atual)
      - onboarding_status (pending/sandbox/active)
      - active_capabilities (já ativadas)
      - capability_cards: lista de cards pra renderizar passo 2
      - next_step: passo recomendado ("vertical" | "capabilities" | "providers" | "activate" | "done")
    """
    identity = await _get_identity_or_404(client_id)

    cards = wizard.build_capability_cards(identity)
    active = identity.capabilities_resolved

    # Determina próximo passo
    if identity.category is None:
        next_step = "vertical"
    elif not active:
        next_step = "capabilities"
    elif any(not c.ready for c in cards if c.capability in active):
        next_step = "providers"
    elif identity.onboarding_status != OnboardingStatus.ACTIVE:
        next_step = "activate"
    else:
        next_step = "done"

    return {
        "client_id": client_id,
        "business_name": identity.business_name,
        "category": identity.category.value if identity.category else None,
        "onboarding_status": identity.onboarding_status.value,
        "owner_phone": identity.owner_phone or "",
        "active_capabilities": [c.value for c in active],
        "capability_cards": [wizard.card_to_dict(c) for c in cards],
        "next_step": next_step,
    }


# ================================================================
# GET /wizard/verticals
# ================================================================


@router.get("/verticals")
async def list_verticals():
    """
    Lista as verticais disponíveis com rótulo amigável.

    Frontend usa pra renderizar o passo 1 (cards de seleção).
    """
    labels = {
        "clinica": "Clínica / consultório",
        "ecommerce": "E-commerce",
        "imobiliaria": "Imobiliária",
        "servicos": "Serviços gerais",
        "educacao": "Educação / cursos",
        "restaurante": "Restaurante",
        "salao_barbearia": "Salão / barbearia",
        "advocacia_financeiro": "Advocacia / financeiro",
        "academia_personal": "Academia / personal",
        "pet": "Pet",
        "automotivo": "Automotivo",
        "outros": "Outros",
    }
    return {
        "verticals": [
            {"slug": c.value, "label": labels.get(c.value, c.value.capitalize())}
            for c in BusinessCategory
        ]
    }


# ================================================================
# POST /wizard/{client_id}/vertical
# ================================================================


@router.post("/{client_id}/vertical")
async def select_vertical(client_id: str, payload: VerticalSelection):
    """
    Define a vertical do cliente. Limpa capabilities ativas (vai re-escolher
    no próximo passo conforme a vertical nova).
    """
    identity = await _get_identity_or_404(client_id)
    category = _parse_vertical(payload.vertical)

    await db.update_client(client_id, {
        "category": category.value,
        "capabilities": None,  # reset — escolher de novo conforme vertical
    })
    log.info(f"Wizard vertical | client={client_id} | category={category.value}")

    # Devolve recomendação pra o frontend já pré-marcar
    recommended = wizard.recommend_capabilities(category)
    return {
        "status": "ok",
        "category": category.value,
        "recommended_capabilities": [c.value for c in recommended],
    }


# ================================================================
# POST /wizard/{client_id}/capabilities
# ================================================================


@router.post("/{client_id}/capabilities")
async def set_capabilities(client_id: str, payload: CapabilitiesSelection):
    """
    Ativa o set de capabilities pedido.

    Validação à prova de bala:
      - Cada capability tem que estar disponível pra a vertical
      - Cada capability tem que ter providers conectados

    Recusa com HTTP 400 + mensagem clara se algo faltar.
    """
    identity = await _get_identity_or_404(client_id)
    if identity.category is None:
        raise HTTPException(400, "Escolha uma vertical antes de ativar capabilities.")

    requested = {_parse_capability(s) for s in payload.capabilities}

    ok, error_msg = wizard.validate_activation(identity, requested)
    if not ok:
        raise HTTPException(400, error_msg)

    await db.update_client(client_id, {
        "capabilities": [c.value for c in requested],
    })
    log.info(
        f"Wizard capabilities | client={client_id} | "
        f"caps={[c.value for c in requested]}"
    )
    return {
        "status": "ok",
        "active_capabilities": [c.value for c in requested],
    }


# ================================================================
# POST /wizard/{client_id}/activate
# ================================================================


@router.post("/{client_id}/activate")
async def activate(client_id: str):
    """
    Última etapa: marca o cliente como ACTIVE (clone começa a responder).

    Recusa se:
      - Sem vertical escolhida
      - Sem capabilities ativas
      - Algum provider necessário ainda desconectado
    """
    identity = await _get_identity_or_404(client_id)

    if identity.category is None:
        raise HTTPException(400, "Escolha uma vertical antes de ativar.")

    active = identity.capabilities_resolved
    if not active:
        raise HTTPException(400, "Ative ao menos uma capability antes de ativar o clone.")

    ok, error_msg = wizard.validate_activation(identity, active)
    if not ok:
        raise HTTPException(400, error_msg)

    await db.update_client(client_id, {
        "onboarding_status": OnboardingStatus.ACTIVE.value,
    })
    log.info(f"Wizard activate | client={client_id} | clone ativo")
    return {
        "status": "ok",
        "onboarding_status": OnboardingStatus.ACTIVE.value,
    }


# ================================================================
# GET /wizard/page — página HTML mínima (estilo OAuth)
# ================================================================


@router.get("/page", response_class=HTMLResponse)
async def wizard_page(client_id: str = Query(..., min_length=1)):
    """
    Página HTML standalone do wizard.

    Reusa o estilo escuro das páginas OAuth. Faz fetch dos endpoints
    desse mesmo router pra renderizar os passos dinamicamente.
    Substitui temporariamente um dashboard real até ele existir.
    """
    return HTMLResponse(content=_render_wizard_html(client_id), status_code=200)


# ================================================================
# HTML do wizard (template inline)
# ================================================================


def _render_wizard_html(client_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HUMA IA — Configuração do clone</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, system-ui, sans-serif;
      background: #0f172a; color: #e2e8f0;
      margin: 0; padding: 40px 16px;
      min-height: 100vh;
    }}
    .wrap {{ max-width: 720px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    .sub {{ color: #94a3b8; margin: 0 0 32px; }}
    .step {{
      background: #1e293b; border-radius: 12px; padding: 24px;
      margin-bottom: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.3);
    }}
    .step-title {{ font-size: 18px; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }}
    .step-num {{
      width: 24px; height: 24px; border-radius: 12px;
      background: #334155; color: #cbd5e1;
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 13px; font-weight: 600;
    }}
    .step-num.done {{ background: #22c55e; color: #022c1a; }}
    .step-num.current {{ background: #3b82f6; color: #fff; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; margin-top: 12px; }}
    .pill {{
      background: #0f172a; border: 1px solid #334155;
      padding: 10px 12px; border-radius: 8px;
      cursor: pointer; text-align: center; font-size: 14px;
      transition: all 0.15s;
    }}
    .pill:hover {{ border-color: #3b82f6; background: #1e293b; }}
    .pill.selected {{ background: #1d4ed8; border-color: #3b82f6; color: #fff; }}
    .cap {{
      background: #0f172a; border: 1px solid #334155;
      padding: 16px; border-radius: 8px; margin-bottom: 10px;
      display: flex; gap: 12px; align-items: flex-start;
    }}
    .cap input {{ margin-top: 4px; transform: scale(1.3); }}
    .cap .headline {{ font-weight: 600; }}
    .cap .desc {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
    .cap .status {{ margin-top: 8px; font-size: 13px; }}
    .ready {{ color: #22c55e; }}
    .blocked {{ color: #f59e0b; }}
    .blocked a {{ color: #fbbf24; text-decoration: underline; }}
    button.primary {{
      background: #22c55e; color: #022c1a; border: 0;
      padding: 12px 24px; border-radius: 8px; font-weight: 600;
      cursor: pointer; font-size: 15px;
    }}
    button.primary:disabled {{ background: #334155; color: #94a3b8; cursor: not-allowed; }}
    button.secondary {{
      background: transparent; border: 1px solid #334155;
      color: #cbd5e1; padding: 10px 16px; border-radius: 8px;
      cursor: pointer; margin-right: 8px;
    }}
    .err {{ background: #7f1d1d; padding: 12px; border-radius: 8px; margin-top: 12px; color: #fecaca; }}
    .ok {{ background: #14532d; padding: 12px; border-radius: 8px; margin-top: 12px; color: #bbf7d0; }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1 id="biz">Configuração do clone</h1>
    <p class="sub">Em 3 passos rápidos, sua IA vai começar a vender no WhatsApp.</p>

    <div class="step">
      <h2 class="step-title"><span id="n1" class="step-num">1</span> Tipo de negócio</h2>
      <div id="verticals" class="grid"></div>
    </div>

    <div class="step" id="step-caps">
      <h2 class="step-title"><span id="n2" class="step-num">2</span> O que sua IA vai fazer</h2>
      <p class="sub" style="margin: 0 0 12px;">Marque o que precisa. Recomendados pré-marcados pra sua vertical.</p>
      <div id="caps"></div>
      <button class="primary" id="save-caps" style="margin-top: 16px;">Salvar e continuar</button>
    </div>

    <div class="step" id="step-final">
      <h2 class="step-title"><span id="n3" class="step-num">3</span> Ativar o clone</h2>
      <p class="sub" style="margin: 0 0 12px;">Quando você ativar, a IA começa a responder no WhatsApp.</p>
      <button class="primary" id="activate-btn">Ativar clone</button>
    </div>

    <div id="msg"></div>
  </div>

<script>
const CLIENT_ID = {client_id!r};
const $ = (id) => document.getElementById(id);

async function loadState() {{
  const r = await fetch(`/wizard/${{CLIENT_ID}}/state`);
  if (!r.ok) {{ showErr("Cliente não encontrado: " + CLIENT_ID); return null; }}
  return await r.json();
}}

function showErr(msg) {{ $("msg").innerHTML = `<div class="err">${{msg}}</div>`; }}
function showOk(msg) {{ $("msg").innerHTML = `<div class="ok">${{msg}}</div>`; }}

async function loadVerticals(currentVertical) {{
  const r = await fetch("/wizard/verticals");
  const data = await r.json();
  $("verticals").innerHTML = data.verticals.map(v =>
    `<div class="pill ${{v.slug === currentVertical ? 'selected' : ''}}" data-slug="${{v.slug}}">${{v.label}}</div>`
  ).join("");
  document.querySelectorAll(".pill").forEach(el => {{
    el.addEventListener("click", () => selectVertical(el.dataset.slug));
  }});
}}

async function selectVertical(slug) {{
  const r = await fetch(`/wizard/${{CLIENT_ID}}/vertical`, {{
    method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{vertical: slug}}),
  }});
  if (!r.ok) {{ showErr(await r.text()); return; }}
  await render();
}}

function renderCaps(state) {{
  if (!state.capability_cards.length) {{
    $("caps").innerHTML = '<p class="sub">Escolha uma vertical primeiro.</p>';
    return;
  }}
  $("caps").innerHTML = state.capability_cards.map(c => `
    <label class="cap">
      <input type="checkbox" data-cap="${{c.capability}}" ${{c.recommended ? 'checked' : ''}}>
      <div>
        <div class="headline">${{c.headline}}</div>
        <div class="desc">${{c.description}}</div>
        <div class="status">${{
          c.ready
            ? '<span class="ready">✓ Pronto pra usar</span>'
            : `<span class="blocked">⚠ Falta conectar: ${{c.blocking_providers.map(p=>p.label).join(', ')}}</span>`
        }}</div>
      </div>
    </label>
  `).join("");
}}

async function saveCaps() {{
  const selected = Array.from(document.querySelectorAll('input[data-cap]:checked'))
    .map(el => el.dataset.cap);
  const r = await fetch(`/wizard/${{CLIENT_ID}}/capabilities`, {{
    method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{capabilities: selected}}),
  }});
  if (!r.ok) {{ showErr((await r.json()).detail || "Erro ao salvar"); return; }}
  showOk("Capabilities salvas.");
  await render();
}}

async function activate() {{
  const r = await fetch(`/wizard/${{CLIENT_ID}}/activate`, {{method: "POST"}});
  if (!r.ok) {{ showErr((await r.json()).detail || "Erro ao ativar"); return; }}
  showOk("✓ Clone ativado! Já está respondendo no WhatsApp.");
  await render();
}}

function setStep(currentStep) {{
  const map = {{"vertical": 1, "capabilities": 2, "providers": 2, "activate": 3, "done": 3}};
  const cur = map[currentStep] || 1;
  [1, 2, 3].forEach(n => {{
    const el = $("n" + n);
    el.className = "step-num " + (n < cur ? "done" : n === cur ? "current" : "");
    el.textContent = n < cur ? "✓" : n;
  }});
  $("step-caps").className = "step" + (cur >= 2 ? "" : " hidden");
  $("step-final").className = "step" + (cur >= 3 ? "" : " hidden");
}}

async function render() {{
  const state = await loadState();
  if (!state) return;
  $("biz").textContent = state.business_name || "Configuração do clone";
  await loadVerticals(state.category);
  renderCaps(state);
  setStep(state.next_step);
}}

$("save-caps").addEventListener("click", saveCaps);
$("activate-btn").addEventListener("click", activate);
render();
</script>
</body>
</html>"""
