# ================================================================
# huma/routes/store_checkout_page.py — Caixinha da HUMA
#
#   GET  /pedido/{token}  → página segura com nome/foto da loja: nome,
#                           e-mail, CPF, CEP (preenche o endereço), número,
#                           forma de pagamento e parcelas
#   POST /pedido/{token}  → gera a cobrança (mesmo motor da conversa) e
#                           mostra o Pix copia e cola / link do cartão;
#                           a confirmação chega na conversa
#
# Nada de segredo aqui: o token é aleatório, vale 24h e morre ao pagar.
# ================================================================

from __future__ import annotations

import html
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from huma.core import store_checkout as sc
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("checkout_page")
router = APIRouter(tags=["Caixinha"])

_rate: dict[str, list[float]] = {}


def _ok_rate(ip: str, limit: int = 30, window: int = 60) -> bool:
    now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < window]
    if len(hits) >= limit:
        _rate[ip] = hits
        return False
    hits.append(now)
    _rate[ip] = hits
    return True


_CSS = """
  :root{--paper:#F6F2EC;--raised:#FBF8F3;--edge:#E5DED1;--ink:#1C1714;--ink2:#3A332D;--ink3:#6B6259;--acc:#C8553D;--acc2:#8E3724;--sage:#5F7A5E;--sagetint:#EAF0E7;}
  *{box-sizing:border-box} body{margin:0;background:var(--paper);color:var(--ink);font-family:'Geist',ui-sans-serif,system-ui,-apple-system,sans-serif;padding:16px;min-height:100vh;display:flex;justify-content:center;align-items:flex-start}
  .box{width:min(480px,100%);background:var(--raised);border:1px solid var(--edge);border-radius:18px;box-shadow:0 12px 40px rgba(28,23,20,.08);overflow:hidden}
  .head{display:flex;gap:14px;align-items:center;padding:18px 20px;border-bottom:1px solid var(--edge)}
  .head img{width:64px;height:64px;border-radius:12px;object-fit:cover;background:var(--paper)}
  .head .ph{width:64px;height:64px;border-radius:12px;background:var(--paper);display:flex;align-items:center;justify-content:center;font-size:26px}
  .head .t{font-weight:600;font-size:16px;letter-spacing:-.01em} .head .s{color:var(--ink3);font-size:13px;margin-top:2px}
  .store{font-size:12px;color:var(--ink3);padding:10px 20px 0;letter-spacing:.04em;text-transform:uppercase}
  form{padding:14px 20px 22px;display:grid;gap:12px}
  label{display:grid;gap:5px;font-size:12.5px;color:var(--ink2);font-weight:500}
  input,select{width:100%;padding:11px 12px;border:1px solid var(--edge);border-radius:11px;background:#fff;font:inherit;font-size:15px;color:var(--ink)}
  input:focus,select:focus{outline:2px solid var(--acc);outline-offset:1px;border-color:var(--acc)}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:2fr 1fr;gap:10px}
  .addr{font-size:13px;color:var(--ink3);min-height:18px}
  .pay{display:grid;gap:8px} .pay label{display:flex;gap:10px;align-items:center;border:1px solid var(--edge);border-radius:11px;padding:10px 12px;font-size:14px;color:var(--ink);cursor:pointer}
  .pay input{width:auto}
  button{margin-top:6px;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:13px;font:inherit;font-size:15px;font-weight:600;cursor:pointer} button:hover{background:var(--acc2)} button:disabled{opacity:.6;cursor:wait}
  .total{display:flex;justify-content:space-between;font-size:14px;padding-top:6px;border-top:1px dashed var(--edge)} .total b{font-size:16px}
  .err{color:#B33A18;font-size:13px;min-height:16px}
  .done{padding:22px 20px;display:grid;gap:12px;text-align:center} .done h2{margin:0;font-size:19px} .done p{margin:0;color:var(--ink2);font-size:14px;line-height:1.55}
  .pix{background:#fff;border:1px solid var(--edge);border-radius:12px;padding:12px;font-family:ui-monospace,monospace;font-size:12px;word-break:break-all;text-align:left}
  .qr{width:200px;height:200px;margin:0 auto;border-radius:12px;border:1px solid var(--edge)}
  a.btn{display:inline-block;background:var(--acc);color:#fff;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:600}
  .safe{font-size:12px;color:var(--ink3);text-align:center;padding:0 20px 18px}
  .ok{background:var(--sagetint);color:var(--sage);border-radius:50%;width:52px;height:52px;display:flex;align-items:center;justify-content:center;font-size:24px;margin:0 auto}
"""

_FONTS = '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap" rel="stylesheet">'


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><link rel="icon" href="/favicon.svg" type="image/svg+xml">{_FONTS}<style>{_CSS}</style></head>
<body><div class="box">{body}</div></body></html>"""


def _price(cents: int) -> str:
    from huma.core.stock_preflight import format_price_brl
    return format_price_brl(cents)


def render_form(token: str, data: dict, identity) -> str:
    """HTML da Caixinha (puro, testável)."""
    name = html.escape(str(data.get("name") or "Produto"))
    qty = int(data.get("qty") or 1)
    price = int(data.get("price_cents") or 0)
    ship = sc.shipping_cents(identity)
    total = sc.total_cents(price, qty, ship)
    img = str(data.get("image_url") or "").strip()
    head_img = f'<img src="{html.escape(img)}" alt="">' if img else '<div class="ph">🛍️</div>'
    store = html.escape(getattr(identity, "business_name", "") or "Loja")
    accepted = [m for m in (getattr(identity, "accepted_payment_methods", None) or []) if m in ("pix", "credit_card", "boleto")] or ["pix"]
    labels = {"pix": "Pix (na hora)", "credit_card": "Cartão de crédito", "boleto": "Boleto"}
    pay_opts = "".join(
        f'<label><input type="radio" name="payment_method" value="{m}" {"checked" if i == 0 else ""}> {labels[m]}</label>'
        for i, m in enumerate(accepted)
    )
    max_inst = max(1, int(getattr(identity, "max_installments", 1) or 1))
    inst_opts = "".join(f'<option value="{n}">{n}x de {_price(round(total / n))}</option>' for n in range(1, max_inst + 1))
    frete = "frete grátis" if ship == 0 else f"frete {_price(ship)}"
    return _shell(f"Finalizar pedido · {store}", f"""
<div class="store">{store} · pagamento seguro pela HUMA</div>
<div class="head">{head_img}<div><div class="t">{name} x{qty}</div><div class="s">{_price(price * qty)} · {frete}</div></div></div>
<form id="f" autocomplete="on">
  <label>Nome completo<input name="lead_name" required autocomplete="name" placeholder="Como está no documento"></label>
  <label>E-mail<input name="lead_email" type="email" required autocomplete="email" placeholder="pra receber o pedido"></label>
  <label>CPF<input name="cpf" inputmode="numeric" autocomplete="off" placeholder="000.000.000-00"></label>
  <div class="row3"><label>CEP<input name="cep" id="cep" inputmode="numeric" required placeholder="00000-000"></label><label>Número<input name="number" required placeholder="nº"></label></div>
  <div class="addr" id="addr"></div>
  <label>Complemento<input name="complement" placeholder="apto, bloco (opcional)"></label>
  <div class="pay">{pay_opts}</div>
  <label id="instw" style="display:none">Parcelas<select name="installments">{inst_opts}</select></label>
  <div class="total"><span>Total</span><b>{_price(total)}</b></div>
  <div class="err" id="err"></div>
  <button type="submit" id="go">Gerar pagamento</button>
</form>
<div class="safe">Seus dados vão só pra {store}. A HUMA não guarda o seu cartão.</div>
<script>
(function(){{
  var f=document.getElementById('f'),err=document.getElementById('err'),go=document.getElementById('go'),cep=document.getElementById('cep'),addr=document.getElementById('addr'),instw=document.getElementById('instw');
  function pm(){{var r=f.querySelector('input[name=payment_method]:checked');return r?r.value:'pix'}}
  f.addEventListener('change',function(){{instw.style.display=pm()==='credit_card'?'grid':'none'}});
  cep.addEventListener('blur',function(){{var c=cep.value.replace(/\\D/g,'');if(c.length!==8)return;addr.textContent='Buscando endereço…';
    fetch('https://viacep.com.br/ws/'+c+'/json/').then(function(r){{return r.json()}}).then(function(d){{addr.textContent=d.erro?'CEP não encontrado':(d.logradouro||'')+(d.bairro?', '+d.bairro:'')+' · '+d.localidade+'/'+d.uf}}).catch(function(){{addr.textContent=''}});}});
  f.addEventListener('submit',function(ev){{ev.preventDefault();err.textContent='';go.disabled=true;go.textContent='Gerando…';
    var body={{}};new FormData(f).forEach(function(v,k){{body[k]=v}});
    fetch(location.pathname,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}})
      .then(function(r){{return r.text()}}).then(function(h){{document.open();document.write(h);document.close();}})
      .catch(function(){{err.textContent='Não consegui gerar agora. Tente de novo.';go.disabled=false;go.textContent='Gerar pagamento';}});
  }});
}})();
</script>""")


def render_done(out: dict, identity) -> str:
    """HTML pós-cobrança: Pix copia e cola + QR, ou botão do cartão/boleto."""
    store = html.escape(getattr(identity, "business_name", "") or "Loja")
    amount = html.escape(out.get("amount_display") or "")
    method = out.get("method", "pix")
    if method == "pix" and out.get("qr_code_text"):
        qr = f'<img class="qr" src="data:image/png;base64,{out["qr_code_base64"]}" alt="QR Pix">' if out.get("qr_code_base64") else ""
        body = f"""<div class="done"><div class="ok">✓</div><h2>Pix de {amount} gerado</h2>
<p>Pague pelo app do seu banco. Assim que cair, o pedido é criado em <b>{store}</b> e a confirmação chega na sua conversa.</p>
{qr}<div class="pix" id="pix">{html.escape(out["qr_code_text"])}</div>
<a class="btn" href="#" onclick="navigator.clipboard&&navigator.clipboard.writeText(document.getElementById('pix').textContent);this.textContent='Código copiado';return false;">Copiar código Pix</a></div>"""
    elif out.get("checkout_url"):
        label = "Pagar com cartão" if method == "credit_card" else "Abrir boleto"
        body = f"""<div class="done"><div class="ok">✓</div><h2>Pedido de {amount} pronto</h2>
<p>Finalize o pagamento no ambiente seguro. Assim que confirmar, o pedido é criado em <b>{store}</b> e a confirmação chega na sua conversa.</p>
<a class="btn" href="{html.escape(out["checkout_url"])}">{label}</a></div>"""
    else:
        body = f"""<div class="done"><div class="ok">✓</div><h2>Cobrança de {amount} enviada</h2>
<p>Ela está na sua conversa com <b>{store}</b>. Volte pro chat pra pagar.</p></div>"""
    return _shell(f"Pagamento · {store}", body)


def render_error(title: str, detail: str) -> str:
    return _shell(title, f'<div class="done"><h2>{html.escape(title)}</h2><p>{html.escape(detail)}</p></div>')


@router.get("/pedido/{token}", response_class=HTMLResponse, include_in_schema=False)
async def checkout_page(token: str, request: Request) -> HTMLResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip):
        return HTMLResponse(render_error("Muitas tentativas", "Espere um instante e abra o link de novo."), status_code=429)
    data = await sc.load_checkout_token(token)
    if not data:
        return HTMLResponse(render_error("Link expirado", "Volte pra conversa e peça um novo link pra finalizar o pedido."), status_code=404)
    identity = await db.get_client(data["client_id"])
    if identity is None:
        return HTMLResponse(render_error("Loja não encontrada", ""), status_code=404)
    return HTMLResponse(render_form(token, data, identity))


@router.post("/pedido/{token}", response_class=HTMLResponse, include_in_schema=False)
async def checkout_submit(token: str, request: Request) -> HTMLResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip, limit=10):
        return HTMLResponse(render_error("Muitas tentativas", "Espere um instante e tente de novo."), status_code=429)
    try:
        form = await request.json()
    except Exception:
        form = {}
    if not isinstance(form, dict):
        form = {}
    data = await sc.load_checkout_token(token)
    if not data:
        return HTMLResponse(render_error("Link expirado", "Volte pra conversa e peça um novo link."), status_code=404)
    identity = await db.get_client(data["client_id"])
    if identity is None:
        return HTMLResponse(render_error("Loja não encontrada", ""), status_code=404)
    out = await sc.submit_checkout(token, form)
    status = out.get("status")
    if status == "pix_sent":
        log.info(f"Caixinha | pedido cobrado | client={data['client_id']} | phone={data['phone']} | {out.get('method')}")
        return HTMLResponse(render_done(out, identity))
    if status == "missing":
        return HTMLResponse(render_error("Faltou um dado", "Preencha: " + ", ".join(out.get("missing") or []) + "."), status_code=400)
    if status == "unavailable":
        return HTMLResponse(render_error("Produto indisponível", "Esse item acabou de sair do estoque. Volte pra conversa que a HUMA te mostra outra opção."), status_code=409)
    if status == "disabled":
        return HTMLResponse(render_error("Pedido na conversa desligado", "A loja não fecha pedidos por aqui no momento."), status_code=400)
    return HTMLResponse(render_error("Não deu certo", "Tente de novo em instantes ou volte pra conversa."), status_code=500)
