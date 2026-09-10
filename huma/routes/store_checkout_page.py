# ================================================================
# huma/routes/store_checkout_page.py — Caixinha da HUMA (v2, uma página só)
#
#   GET  /pedido/{token}         → página com a marca da loja: dados +
#                                  pagamento na MESMA tela (Pix com QR, ou
#                                  cartão de crédito/débito digitado ali,
#                                  tokenizado no navegador pelo Mercado Pago)
#   POST /pedido/{token}/pix     → gera o Pix (JSON: QR + copia e cola)
#   POST /pedido/{token}/card    → cobra o cartão na hora (JSON: aprovado /
#                                  em análise / recusado com motivo)
#   GET  /pedido/{token}/status  → poll: pagamento caiu? pedido criado?
#
# Regra: enquanto o lead está aqui, NADA chega na conversa. Só depois de
# pago: "Pedido #N criado". Token aleatório, 24h, rate limit por IP.
# ================================================================

from __future__ import annotations

import html
import json as _json
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from huma.core import store_checkout as sc
from huma.config import MERCADOPAGO_PUBLIC_KEY
from huma.services import db_service as db
from huma.utils.logger import get_logger

log = get_logger("checkout_page")
router = APIRouter(tags=["Caixinha"])

_rate: dict[str, list[float]] = {}


def _ok_rate(ip: str, limit: int = 60, window: int = 60) -> bool:
    now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < window]
    if len(hits) >= limit:
        _rate[ip] = hits
        return False
    hits.append(now)
    _rate[ip] = hits
    return True


_CSS = """
  :root{--paper:#F6F2EC;--raised:#FBF8F3;--edge:#E5DED1;--ink:#1C1714;--ink2:#3A332D;--ink3:#6B6259;--acc:#C8553D;--acc2:#8E3724;--sage:#5F7A5E;--sagetint:#EAF0E7;--ember:#B33A18;}
  *{box-sizing:border-box} body{margin:0;background:var(--paper);color:var(--ink);font-family:'Geist',ui-sans-serif,system-ui,-apple-system,sans-serif;padding:16px;min-height:100vh;display:flex;justify-content:center;align-items:flex-start}
  .box{width:min(480px,100%);background:var(--raised);border:1px solid var(--edge);border-radius:18px;box-shadow:0 12px 40px rgba(28,23,20,.08);overflow:hidden}
  .head{display:flex;gap:14px;align-items:center;padding:18px 20px;border-bottom:1px solid var(--edge)}
  .head img{width:64px;height:64px;border-radius:12px;object-fit:cover;background:var(--paper)}
  .head .ph{width:64px;height:64px;border-radius:12px;background:var(--paper);display:flex;align-items:center;justify-content:center;font-size:26px}
  .head .t{font-weight:600;font-size:16px;letter-spacing:-.01em} .head .s{color:var(--ink3);font-size:13px;margin-top:2px}
  .store{font-size:12px;color:var(--ink3);padding:10px 20px 0;letter-spacing:.04em;text-transform:uppercase}
  .sec{padding:14px 20px 4px;display:grid;gap:12px}
  .sec h3{margin:6px 0 0;font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--ink3);font-weight:600}
  label{display:grid;gap:5px;font-size:12.5px;color:var(--ink2);font-weight:500}
  input,select{width:100%;padding:11px 12px;border:1px solid var(--edge);border-radius:11px;background:#fff;font:inherit;font-size:15px;color:var(--ink)}
  input:focus,select:focus{outline:2px solid var(--acc);outline-offset:1px;border-color:var(--acc)}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:2fr 1fr;gap:10px}
  .addr{font-size:13px;color:var(--ink3);min-height:18px}
  .tabs{display:grid;grid-template-columns:1fr 1fr;gap:8px} .tab{border:1px solid var(--edge);border-radius:11px;padding:11px;text-align:center;font-size:14px;cursor:pointer;background:#fff;color:var(--ink)}
  .tab.on{border-color:var(--acc);background:#FBEDE7;color:var(--acc2);font-weight:600}
  .pane{display:none;gap:12px} .pane.on{display:grid}
  button.go{margin:8px 0 0;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:13px;font:inherit;font-size:15px;font-weight:600;cursor:pointer;width:100%} button.go:hover{background:var(--acc2)} button.go:disabled{opacity:.6;cursor:wait}
  .total{display:flex;justify-content:space-between;font-size:14px;padding:10px 0 0;border-top:1px dashed var(--edge)} .total b{font-size:16px}
  .err{color:var(--ember);font-size:13px;min-height:16px}
  .done{padding:22px 20px;display:grid;gap:12px;text-align:center} .done h2{margin:0;font-size:19px} .done p{margin:0;color:var(--ink2);font-size:14px;line-height:1.55}
  .pix{background:#fff;border:1px solid var(--edge);border-radius:12px;padding:12px;font-family:ui-monospace,monospace;font-size:12px;word-break:break-all;text-align:left}
  .qr{width:200px;height:200px;margin:0 auto;border-radius:12px;border:1px solid var(--edge)}
  .btn{display:inline-block;background:var(--acc);color:#fff;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:600;border:0;font:inherit;cursor:pointer}
  .safe{font-size:12px;color:var(--ink3);text-align:center;padding:12px 20px 18px}
  .ok{background:var(--sagetint);color:var(--sage);border-radius:50%;width:52px;height:52px;display:flex;align-items:center;justify-content:center;font-size:24px;margin:0 auto}
  .wait{color:var(--ink3);font-size:13px}
"""

_FONTS = '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap" rel="stylesheet">'


def _shell(title: str, body: str, extra_head: str = "") -> str:
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><link rel="icon" href="/favicon.svg" type="image/svg+xml">{_FONTS}{extra_head}<style>{_CSS}</style></head>
<body><div class="box">{body}</div></body></html>"""


def _price(cents: int) -> str:
    from huma.core.stock_preflight import format_price_brl
    return format_price_brl(cents)


def render_form(token: str, data: dict, identity, prefill: dict | None = None) -> str:
    """
    HTML da Caixinha (puro, testável): dados + pagamento numa tela.

    Universal (2026-09-10): origem "store" (produto da loja, com entrega e
    cupom da loja) ou "custom" (serviço/valor combinado: sem endereço quando
    needs_shipping=False, sem cupom da loja). `prefill` = o que o lead já
    digitou numa compra anterior (segunda compra é um toque).
    """
    prefill = prefill if isinstance(prefill, dict) else {}
    origin = str(data.get("origin") or sc.ORIGIN_STORE)
    is_store = origin == sc.ORIGIN_STORE
    needs_shipping = bool(data.get("needs_shipping", is_store))
    name = html.escape(str(data.get("name") or "Produto"))
    qty = int(data.get("qty") or 1)
    price = int(data.get("price_cents") or 0)
    ship = sc.shipping_cents(identity) if needs_shipping else 0
    total = sc.total_cents(price, qty, ship)
    img = str(data.get("image_url") or "").strip()
    head_img = f'<img src="{html.escape(img)}" alt="">' if img else f'<div class="ph">{"🛍️" if is_store else "✓"}</div>'
    store = html.escape(getattr(identity, "business_name", "") or "Loja")
    accepted = [m for m in (getattr(identity, "accepted_payment_methods", None) or []) if m in ("pix", "credit_card")] or ["pix"]
    max_inst = max(1, int(getattr(identity, "max_installments", 1) or 1))
    inst_opts = "".join(f'<option value="{n}">{n}x de {_price(round(total / n))}{" sem juros" if n > 1 else ""}</option>' for n in range(1, max_inst + 1))
    if needs_shipping:
        frete = "frete grátis" if ship == 0 else f"frete {_price(ship)}"
    else:
        frete = html.escape(str(data.get("description") or "")[:60]) or "pagamento seguro"
    # Trilho (2026-09-10): Mercado Pago tokeniza no navegador (public key da
    # conta certa); Asaas recebe o cartão só em trânsito e exige CPF, CEP,
    # número e celular do titular.
    rail = sc.payment_rail(identity)
    is_asaas = rail == sc.RAIL_ASAAS
    public_key = "" if is_asaas else ((getattr(identity, "mercadopago_public_key", "") or "").strip() or MERCADOPAGO_PUBLIC_KEY)
    has_pix = "pix" in accepted
    has_card = "credit_card" in accepted and (is_asaas or bool(public_key))
    tabs = ""
    if has_pix and has_card:
        tabs = '<div class="tabs"><div class="tab on" data-t="pix">Pix</div><div class="tab" data-t="card">Cartão</div></div>'
    first = "pix" if has_pix else "card"
    pix_pane = f"""<div class="pane {'on' if first == 'pix' else ''}" id="pane-pix">
  <div class="wait">Você recebe o QR code aqui mesmo e paga pelo app do banco. Cai na hora.</div>
  <button type="button" class="go" id="btn-pix">Gerar Pix de {_price(total)}</button></div>""" if has_pix else ""
    def _v(key: str) -> str:
        return html.escape(str(prefill.get(key) or ""), quote=True)

    billing_block = ("" if needs_shipping else """
  <div class="row3"><label>CEP do titular<input name="cep" id="cep" inputmode="numeric" placeholder="00000-000" value="{cepv}"></label><label>Número<input name="number" placeholder="nº" value="{numv}"></label></div>""".format(cepv=_v("cep"), numv=_v("number"))) if is_asaas else ""
    phone_block = f"""
  <label>Celular do titular<input name="phone" inputmode="tel" autocomplete="tel" placeholder="(11) 99999-9999" value="{_v('phone')}"></label>""" if is_asaas else ""
    card_note = ("Crédito. O cartão vai criptografado e é cobrado na hora; a HUMA não guarda o número."
                 if is_asaas else "Crédito ou débito. O número do cartão vai criptografado direto pro Mercado Pago; a HUMA não vê nem guarda.")
    card_pane = f"""<div class="pane {'on' if first == 'card' else ''}" id="pane-card">
  <label>Número do cartão<input id="cnum" inputmode="numeric" autocomplete="cc-number" placeholder="0000 0000 0000 0000"></label>
  <label>Nome no cartão<input id="cname" autocomplete="cc-name" placeholder="Como está no cartão"></label>
  <div class="row"><label>Validade<input id="cexp" inputmode="numeric" autocomplete="cc-exp" placeholder="MM/AA"></label><label>CVV<input id="ccvv" inputmode="numeric" autocomplete="cc-csc" placeholder="123"></label></div>{billing_block}{phone_block}
  <label>Parcelas<select id="inst">{inst_opts}</select></label>
  <div class="wait">{card_note}</div>
  <button type="button" class="go" id="btn-card">Pagar {_price(total)}</button></div>""" if has_card else ""

    address_block = f"""
  <div class="row3"><label>CEP<input name="cep" id="cep" inputmode="numeric" required placeholder="00000-000" value="{_v('cep')}"></label><label>Número<input name="number" required placeholder="nº" value="{_v('number')}"></label></div>
  <div class="addr" id="addr"></div>
  <label>Complemento<input name="complement" placeholder="apto, bloco (opcional)" value="{_v('complement')}"></label>""" if needs_shipping else ""
    coupon_block = """
<div class="sec"><h3>Cupom</h3>
  <div class="row3"><label>Código<input name="coupon" id="coupon" autocomplete="off" placeholder="tem cupom?" style="text-transform:uppercase"></label><label>&nbsp;<button type="button" class="go" id="btn-coupon" style="margin:0;padding:11px">Aplicar</button></label></div>
  <div class="addr" id="coupon-msg"></div>
</div>""" if is_store else ""
    item_line = f"{name} x{qty}" if (is_store or qty > 1) else name
    lembrado = '<div class="addr">Seus dados da última compra já estão aqui. Confere e paga.</div>' if prefill.get("lead_name") else ""
    titulo = "Finalizar pedido" if needs_shipping else "Pagar"
    body = f"""
<div class="store">{store} · pagamento seguro pela HUMA</div>
<div class="head">{head_img}<div><div class="t">{item_line}</div><div class="s">{_price(price * qty)} · {frete}</div></div></div>
<form id="f" autocomplete="on" onsubmit="return false">
<div class="sec"><h3>Seus dados</h3>{lembrado}
  <label>Nome completo<input name="lead_name" required autocomplete="name" placeholder="Como está no documento" value="{_v('lead_name')}"></label>
  <label>E-mail<input name="lead_email" type="email" required autocomplete="email" placeholder="pra receber a confirmação" value="{_v('lead_email')}"></label>
  <label>CPF<input name="cpf" inputmode="numeric" placeholder="000.000.000-00" value="{_v('cpf')}"></label>{address_block}
</div>{coupon_block}
<div class="sec"><h3>Pagamento</h3>{tabs}{pix_pane}{card_pane}
  <div class="total" id="discount-row" style="display:none;border-top:0;padding-top:0"><span>Desconto</span><b id="discount"></b></div>
  <div class="total"><span>Total</span><b id="total">{_price(total)}</b></div>
  <div class="err" id="err"></div>
</div>
</form>
<div id="result"></div>
<div class="safe">Seus dados vão só pra {store}. Pagamento processado pelo {"Asaas" if is_asaas else "Mercado Pago"}.</div>
<script>
(function(){{
  var T=location.pathname, f=document.getElementById('f'), err=document.getElementById('err'), addr=document.getElementById('addr'), cep=document.getElementById('cep'), res=document.getElementById('result');
  var SHIP={'true' if needs_shipping else 'false'}, STORE={'true' if is_store else 'false'}, RAIL={'"asaas"' if is_asaas else '"mp"'};
  var PK={_json.dumps(public_key or "")}, mp=null;
  try{{ if(PK && window.MercadoPago) mp=new window.MercadoPago(PK); }}catch(e){{}}
  document.querySelectorAll('.tab').forEach(function(t){{t.addEventListener('click',function(){{
    document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('on')}}); t.classList.add('on');
    document.querySelectorAll('.pane').forEach(function(p){{p.classList.remove('on')}});
    document.getElementById('pane-'+t.dataset.t).classList.add('on');
  }});}});
  function lookupCep(){{if(!cep||!addr)return;var c=cep.value.replace(/\\D/g,'');if(c.length!==8)return;addr.textContent='Buscando endereço…';
    fetch('https://viacep.com.br/ws/'+c+'/json/').then(function(r){{return r.json()}}).then(function(d){{addr.textContent=d.erro?'CEP não encontrado':(d.logradouro||'')+(d.bairro?', '+d.bairro:'')+' · '+d.localidade+'/'+d.uf}}).catch(function(){{addr.textContent=''}});}}
  if(cep){{cep.addEventListener('blur',lookupCep); if(cep.value) lookupCep();}}
  function data(){{var b={{}};new FormData(f).forEach(function(v,k){{b[k]=v}});return b;}}
  var TOTAL={total}, MAXI={max_inst};
  function brl(c){{return 'R$ '+(c/100).toFixed(2).replace('.',',').replace(/\\B(?=(\\d{{3}})+(?!\\d))/g,'.');}}
  function setTotal(c,disc){{TOTAL=c;document.getElementById('total').textContent=brl(c);var dr=document.getElementById('discount-row');if(disc>0){{dr.style.display='flex';document.getElementById('discount').textContent='- '+brl(disc);}}else dr.style.display='none';
    var bp=document.getElementById('btn-pix');if(bp)bp.textContent='Gerar Pix de '+brl(c);var bc=document.getElementById('btn-card');if(bc)bc.textContent='Pagar '+brl(c);
    var sel=document.getElementById('inst');if(sel){{sel.innerHTML='';for(var n=1;n<=MAXI;n++){{var o=document.createElement('option');o.value=n;o.textContent=n+'x de '+brl(Math.round(c/n))+(n>1?' sem juros':'');sel.appendChild(o);}}}}}}
  var bcp=document.getElementById('btn-coupon'), cmsg=document.getElementById('coupon-msg');
  if(bcp) bcp.addEventListener('click',function(){{var code=(document.getElementById('coupon').value||'').trim().toUpperCase();document.getElementById('coupon').value=code;cmsg.textContent='Verificando…';
    post('/coupon',{{coupon:code}}).then(function(o){{if(o.status==='ok'){{setTotal(o.total_cents,o.discount_cents||0);cmsg.textContent=o.discount_cents?('Cupom '+o.coupon+' aplicado ('+o.label+').'):'';}}else{{setTotal(o.total_cents||TOTAL,0);cmsg.textContent=code?'Cupom inválido ou vencido.':'';}}}}).catch(function(){{cmsg.textContent='Não consegui validar agora.';}});}});
  function check(){{var d=data();if(!d.lead_name||!d.lead_email||(SHIP&&(!d.cep||!d.number))){{err.textContent=SHIP?'Preencha nome, e-mail, CEP e número.':'Preencha nome e e-mail.';return null;}}
    if(RAIL==='asaas'&&((d.cpf||'').replace(/\\D/g,'').length!==11)){{err.textContent='Preencha o CPF (11 dígitos).';return null;}}err.textContent='';return d;}}
  function post(path,body){{return fetch(T+path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}).then(function(r){{return r.json()}});}}
  function fail(o){{err.textContent=o.detail||(o.status==='missing'?'Preencha: '+(o.missing||[]).join(', ')+'.':o.status==='unavailable'?'Esse item acabou de sair do estoque.':'Não deu certo agora. Tente de novo.');}}
  function showPix(o){{f.style.display='none';res.innerHTML='<div class="done"><div class="ok">✓</div><h2>Pix de '+o.amount_display+' gerado</h2><p>Pague pelo app do seu banco. Assim que cair, '+(STORE?'o pedido é criado em <b>{store}</b> e ':'')+'você recebe a confirmação aqui e na conversa.</p>'+(o.qr_code_base64?'<img class="qr" src="data:image/png;base64,'+o.qr_code_base64+'" alt="QR Pix">':'')+'<div class="pix" id="pix">'+o.qr_code_text+'</div><button class="btn" id="copy">Copiar código Pix</button><div class="wait" id="w">Aguardando pagamento…</div></div>';
    document.getElementById('copy').addEventListener('click',function(){{navigator.clipboard&&navigator.clipboard.writeText(document.getElementById('pix').textContent);this.textContent='Código copiado';}});
    poll();}}
  function showOk(n,done){{f.style.display='none';
    if(done||!STORE){{res.innerHTML='<div class="done"><div class="ok">✓</div><h2>Pagamento confirmado</h2><p>Tudo certo. A confirmação já está na sua conversa com <b>{store}</b>. Pode voltar pro chat.</p></div>';return;}}
    res.innerHTML='<div class="done"><div class="ok">✓</div><h2>Pagamento confirmado</h2><p>'+(n?'Pedido <b>#'+n+'</b> criado em {store}. A confirmação já está na sua conversa. Pode voltar pro chat.':'{store} está registrando seu pedido. O número chega na sua conversa em instantes.')+'</p></div>';if(!n){{var tries=0;(function again(){{tries++;fetch(T+'/status').then(function(r){{return r.json()}}).then(function(s){{if(s.order_number||s.done)showOk(s.order_number,s.done);else if(tries<20)setTimeout(again,3000);}}).catch(function(){{if(tries<20)setTimeout(again,5000);}});}})();}}}}
  function poll(){{fetch(T+'/status').then(function(r){{return r.json()}}).then(function(s){{if(s.status==='approved'){{showOk(s.order_number,s.done);}}else if(s.status==='rejected'){{var w=document.getElementById('w');if(w)w.textContent='Pagamento não aprovado. Volte pra conversa.';}}else setTimeout(poll,3000);}}).catch(function(){{setTimeout(poll,5000)}});}}
  var bp=document.getElementById('btn-pix'); if(bp) bp.addEventListener('click',function(){{var d=check();if(!d)return;bp.disabled=true;bp.textContent='Gerando…';post('/pix',d).then(function(o){{if(o.status==='ok')showPix(o);else{{fail(o);bp.disabled=false;bp.textContent='Gerar Pix';}}}}).catch(function(){{fail({{}});bp.disabled=false;bp.textContent='Gerar Pix';}});}});
  function cardResult(o){{if(o.status==='approved')showOk(o.order_number,!STORE);else if(o.status==='in_process'){{f.style.display='none';res.innerHTML='<div class="done"><div class="ok">…</div><h2>Pagamento em análise</h2><p>O banco está analisando. Assim que aprovar, '+(STORE?'o pedido é criado em {store} e ':'')+'a confirmação chega na sua conversa.</p></div>';}}else{{fail(o);bc.disabled=false;bc.textContent='Pagar';}}}}
  var bc=document.getElementById('btn-card'); if(bc) bc.addEventListener('click',function(){{var d=check();if(!d)return;
    var num=(document.getElementById('cnum').value||'').replace(/\\s/g,''), nm=document.getElementById('cname').value.trim(), ex=(document.getElementById('cexp').value||'').replace(/\\D/g,''), cv=document.getElementById('ccvv').value.trim(), cpf=(d.cpf||'').replace(/\\D/g,'');
    if(num.length<13){{err.textContent='Confere o número do cartão.';return;}} if(ex.length!==4){{err.textContent='Validade no formato MM/AA.';return;}} if(cv.length<3){{err.textContent='Confere o CVV.';return;}} if(cpf.length!==11){{err.textContent='CPF do titular é obrigatório no cartão.';return;}}
    if(RAIL==='asaas'){{
      if((d.cep||'').replace(/\\D/g,'').length!==8||!d.number){{err.textContent='Preencha o CEP e o número do titular.';return;}}
      if((d.phone||'').replace(/\\D/g,'').length<10){{err.textContent='Preencha o celular do titular.';return;}}
      bc.disabled=true;bc.textContent='Processando…';
      d.card_number=num; d.card_holder=nm; d.card_exp=ex.slice(0,2)+'/'+ex.slice(2); d.card_cvv=cv; d.installments=document.getElementById('inst').value;
      post('/card',d).then(cardResult).catch(function(){{err.textContent='Não deu certo. Tente de novo.';bc.disabled=false;bc.textContent='Pagar';}});
      return;
    }}
    if(!mp){{err.textContent='Cartão indisponível agora. Use Pix.';return;}}
    bc.disabled=true;bc.textContent='Processando…';
    mp.getPaymentMethods({{bin:num.slice(0,6)}}).then(function(pms){{var pm=pms&&pms.results&&pms.results[0];if(!pm)throw new Error('Não reconheci a bandeira. Confere o número.');
      return mp.createCardToken({{cardNumber:num,cardholderName:nm,cardExpirationMonth:ex.slice(0,2),cardExpirationYear:'20'+ex.slice(2),securityCode:cv,identificationType:'CPF',identificationNumber:cpf}}).then(function(tk){{if(!tk||!tk.id)throw new Error('Cartão não validado. Confere os dados.');
        d.card_token_id=tk.id; d.payment_method_id=pm.id; d.issuer_id=(pm.issuer&&pm.issuer.id)||''; d.installments=document.getElementById('inst').value; return post('/card',d);}});
    }}).then(cardResult).catch(function(e){{err.textContent=(e&&e.message)||'Não deu certo. Confere os dados do cartão.';bc.disabled=false;bc.textContent='Pagar';}});
  }});
}})();
</script>"""
    head = '<script src="https://sdk.mercadopago.com/js/v2"></script>' if (has_card and not is_asaas) else ""
    return _shell(f"{titulo} · {store}", body, head)


def render_error(title: str, detail: str) -> str:
    return _shell(title, f'<div class="done"><h2>{html.escape(title)}</h2><p>{html.escape(detail)}</p></div>')


async def _ctx(token: str):
    data = await sc.load_checkout_token(token)
    if not data:
        return None, None
    identity = await db.get_client(data["client_id"])
    return data, identity


@router.get("/pedido/{token}", response_class=HTMLResponse, include_in_schema=False)
async def checkout_page(token: str, request: Request) -> HTMLResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip):
        return HTMLResponse(render_error("Muitas tentativas", "Espere um instante e abra o link de novo."), status_code=429)
    data, identity = await _ctx(token)
    if not data:
        return HTMLResponse(render_error("Link expirado", "Volte pra conversa e peça um novo link pra finalizar o pedido."), status_code=404)
    if identity is None:
        return HTMLResponse(render_error("Loja não encontrada", ""), status_code=404)
    prefill = await sc.checkout_profile(data.get("client_id", ""), data.get("phone", ""))
    return HTMLResponse(render_form(token, data, identity, prefill))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/pedido/{token}/coupon", include_in_schema=False)
async def checkout_coupon(token: str, request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip, limit=30):
        return JSONResponse({"status": "error", "detail": "Muitas tentativas."}, status_code=429)
    out = await sc.quote(token, await _json_body(request))
    return JSONResponse(out, status_code=200 if out.get("status") in ("ok", "invalid_coupon") else 400)


@router.post("/pedido/{token}/pix", include_in_schema=False)
async def checkout_pix(token: str, request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip, limit=20):
        return JSONResponse({"status": "error", "detail": "Muitas tentativas. Espere um instante."}, status_code=429)
    out = await sc.pay_pix(token, await _json_body(request))
    return JSONResponse(out, status_code=200 if out.get("status") == "ok" else 400)


@router.post("/pedido/{token}/card", include_in_schema=False)
async def checkout_card(token: str, request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip, limit=20):
        return JSONResponse({"status": "error", "detail": "Muitas tentativas. Espere um instante."}, status_code=429)
    # IP do lead (o Asaas exige remoteIp na cobrança de cartão); atrás do proxy do Railway vem no X-Forwarded-For
    remote_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or ip)
    out = await sc.pay_card(token, await _json_body(request), remote_ip=remote_ip)
    code = 200 if out.get("status") in ("approved", "in_process", "rejected") else 400
    return JSONResponse(out, status_code=code)


@router.get("/pedido/{token}/status", include_in_schema=False)
async def checkout_status(token: str, request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "?"
    if not _ok_rate(ip, limit=120):
        return JSONResponse({"status": "pending"}, status_code=429)
    return JSONResponse(await sc.payment_status(token))
