# ================================================================
# huma/providers/inventory/lojaintegrada.py: adapter Loja Integrada (Personal token)
#
# Implementa InventoryProvider contra a API v2 da Loja Integrada
# (`https://api.awsli.com.br/v1/`). Sem OAuth: o dono (só o proprietário,
# plano pago) gera o Personal token em Configurações > Chave para API e
# cola na HUMA. Header: `Authorization: Basic {PERSONAL_TOKEN}` (o valor é
# o token tal como o painel entrega). Credencial mora em
# clients.integrations["lojaintegrada"]:
#   {"personal_token", "webhook_token", "meta": {...}}
#
# Endpoints usados (ficha research_notes/integracoes/ecommerce.md, 2026-11-06,
# lida do OpenAPI oficial):
#   GET /categoria/?limit=1                 valida o token
#   GET /produto?ativo=true&limit=50&offset= catálogo (SEM preço/estoque na listagem)
#   GET /produto?sku=                       SKU exato → objects[0]
#   GET /produto/{id}                       preco_cheio, preco_promocional, imagem_principal.grande, url
#   GET /produto_preco/set/{ids}            preço em lote (cheio, promocional)
#   GET /produto_estoque/{id}               gerenciado, quantidade_disponivel, situacao_sem_estoque
#   GET /pedido/{numero}                    pedido (situacao.codigo, cliente, cupom_desconto, itens)
#   PUT {raiz}/webhooks/v1/pedido           {notifyUrl, token}  [NÃO validado]
#   POST /cupom                             cupom único da conversa
#
# Rate limit: 100 req/min POR LOJA (429 com código 633). Por isso o sync
# para em MAX_SYNC_PRODUCTS=60 (listagem + preço em lote, sem estoque) e o
# catálogo fica em cache no processo por CATALOG_TTL segundos; o estoque só
# é lido ao vivo do produto casado no check_stock.
#
# Estoque (regra da ficha): `gerenciado=false` = disponível, quantidade
# desconhecida; esgotado só com gerenciado=true, quantidade_disponivel<=0 e
# situacao_sem_estoque=-1. Nunca marcar esgotado por falta de dado.
# Frete: a LI não cota por API de produto → no_logistics_configured.
# Pedido pago: `situacao.codigo == "pedido_pago"` ou `situacao.aprovado`.
#
# NÃO validado com API real: forma de referenciar o produto na resposta de
# /produto_preco/set (aceita `produto` URI, `produto_id` ou ordem dos ids),
# cadastro de webhook com Personal token (a doc só mostra chave de
# aplicação), header/payload do webhook, formato de `cupom_desconto` no
# pedido (null no exemplo oficial: aceita string, dict e lista).
# Nenhum método propaga exceção: tudo vira {"status": ...}.
# ================================================================

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("lojaintegrada")

INTEGRATION_ID = "lojaintegrada"
MAX_SYNC_PRODUCTS = 60
CATALOG_TTL = 600.0
_DEFAULT_TIMEOUT = 12.0
_PAGE = 50
_PRICE_CHUNK = 20
_STOCK_CHILDREN = 5
_PAID_CODES = {"pedido_pago"}
_SELLABLE_TYPES = {"normal", "atributo", ""}

# Catálogo por loja (chave = hash do token): 100 req/min não aguenta reler a cada pergunta.
_CATALOG_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _base_url() -> str:
    from huma.config import LOJAINTEGRADA_API_BASE_URL
    return (LOJAINTEGRADA_API_BASE_URL or "https://api.awsli.com.br/v1").rstrip("/")


def _cents(value: Any) -> int:
    try:
        return int(round(float(str(value or 0).replace(",", ".")) * 100))
    except (TypeError, ValueError):
        return 0


def _qty(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _phone_e164(value: Any) -> str:
    """Telefone do comprador → dígitos com DDI quando é brasileiro (br_e164)."""
    d = _digits(value)
    if not d:
        return ""
    from huma.services.whatsapp_service import br_e164
    return br_e164(d)


def _id_from_uri(value: Any) -> str:
    """"/api/v1/produto/166737213" (ou int) → "166737213"."""
    if isinstance(value, (int, float)):
        return str(int(value))
    if isinstance(value, dict):
        return str(value.get("id") or _id_from_uri(value.get("resource_uri")) or "")
    m = re.search(r"(\d+)/?$", str(value or ""))
    return m.group(1) if m else ""


def coupon_codes(value: Any) -> list[str]:
    """`cupom_desconto` do pedido (string, dict, lista ou null) → códigos em maiúsculas."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, list):
        for v in value:
            out.extend(coupon_codes(v))
        return out
    if isinstance(value, dict):
        code = value.get("codigo") or value.get("code") or value.get("cupom") or ""
        return [str(code).strip().upper()] if code else []
    code = str(value).strip().upper()
    return [code] if code and code not in ("NONE", "NULL") else []


def order_to_huma(raw: dict, client_id: str = "") -> dict:
    """
    Pedido da Loja Integrada (GET /pedido/{numero}) → formato que
    store_orders.handle_paid_order / match_order / match_customer leem.
    `id` leva o client_id porque o número do pedido é por loja.
    """
    raw = raw if isinstance(raw, dict) else {}
    numero = str(raw.get("numero") or raw.get("id") or "")
    situacao = raw.get("situacao") if isinstance(raw.get("situacao"), dict) else {}
    codigo = str(situacao.get("codigo") or "").lower()
    paid = codigo in _PAID_CODES or situacao.get("aprovado") is True
    cliente = raw.get("cliente") if isinstance(raw.get("cliente"), dict) else {}
    products: list[dict] = []
    for it in raw.get("itens") or []:
        if not isinstance(it, dict):
            continue
        products.append({
            "sku": str(it.get("sku") or ""),
            "name": str(it.get("nome") or ""),
            "quantity": _qty(it.get("quantidade")),
            "price_cents": _cents(it.get("preco_venda")),
            "product_id": _id_from_uri(it.get("produto")),
        })
    try:
        total = float(str(raw.get("valor_total") or 0).replace(",", "."))
    except (TypeError, ValueError):
        total = 0.0
    huma_id = f"li_{client_id}_{numero}" if client_id else f"li_{numero}"
    return {
        "id": huma_id if numero else "",
        "store_order_id": numero,
        "number": numero,
        "status": codigo,
        "paid": paid,
        "contact_email": str(cliente.get("email") or "").strip().lower(),
        "contact_phone": _phone_e164(cliente.get("telefone_celular") or cliente.get("telefone_principal")),
        "contact_name": str(cliente.get("nome") or cliente.get("razao_social") or "").strip(),
        "contact_document": _digits(cliente.get("cpf") or cliente.get("cnpj")),
        "coupon": [{"code": c} for c in coupon_codes(raw.get("cupom_desconto"))],
        "total": total,
        "currency": "BRL",
        "note": "",
        "products": products,
        "store": INTEGRATION_ID,
    }


class LojaIntegradaAdapter(InventoryProvider):
    """Adapter Loja Integrada v2 (Personal token por cliente)."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        personal_token: str = "",
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        self.meta: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
            personal_token = str(creds.get("personal_token") or personal_token or "")
            self.meta = creds.get("meta") if isinstance(creds.get("meta"), dict) else {}
        self.personal_token = (personal_token or "").strip()
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.personal_token)

    @property
    def client_id(self) -> str:
        return str(getattr(self.identity, "client_id", "") or "")

    def _cache_key(self) -> str:
        return hashlib.sha256(self.personal_token.encode("utf-8")).hexdigest()[:16]

    def _root_url(self) -> str:
        """Raiz sem /v1 (os webhooks ficam em /webhooks/v1/...)."""
        return re.sub(r"/v\d+/?$", "", self.base_url)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        absolute: bool = False,
    ) -> tuple[int, Any]:
        """(status, json). status 0 = falha de rede. Nunca levanta. Token nunca vai pro log."""
        if not self._has_creds:
            return 401, None
        url = path if absolute else f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Basic {self.personal_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "HUMA IA (contato@humaia.com.br)",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params or None, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code == 429:
                log.warning(f"Loja Integrada | limite de requisições (100/min por loja) | {method} {path}")
            elif resp.status_code >= 400:
                log.warning(f"Loja Integrada HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=lojaintegrada | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=lojaintegrada | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    def _err(self, st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st == 429:
            return {"status": "error", "detail": "rate_limited"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def validate_credentials(self) -> dict:
        """
        Confere o Personal token (GET /categoria/?limit=1). Contrato do spec:
        {"status": "ok", "label", "meta"} | {"status": "error", "user_message"}.
        """
        if not self._has_creds:
            return {"status": "error", "user_message": "Cole o Personal token da Loja Integrada."}
        st, body = await self._request("GET", "categoria/", params={"limit": 1})
        if st == 200 and isinstance(body, (dict, list)):
            meta = body.get("meta") if isinstance(body, dict) and isinstance(body.get("meta"), dict) else {}
            categorias = _qty(meta.get("total_count"))
            log.info(f"Loja Integrada | credencial ok | categorias={categorias}")
            return {"status": "ok", "label": "Loja Integrada", "meta": {"categorias": categorias}}
        if st == 0:
            return {"status": "error", "user_message": "Não consegui falar com a Loja Integrada agora. Tente de novo em instantes."}
        if st in (401, 403):
            return {"status": "error", "user_message": (
                "A Loja Integrada não reconheceu esse Personal token. Confira em Configurações > Chave para API > "
                "Personal token (só o proprietário da loja gera, e a função exige plano pago)."
            )}
        if st == 429:
            return {"status": "error", "user_message": "A Loja Integrada limitou as consultas desta loja por um minuto. Tente de novo em instantes."}
        return {"status": "error", "user_message": f"A Loja Integrada respondeu com erro {st}. Tente de novo em instantes."}

    # ── normalização ─────────────────────────────────────────────

    @staticmethod
    def _listing_to_dict(p: dict) -> dict:
        """Produto da listagem (sem preço/estoque) → contrato (stock_unknown=True)."""
        filhos = [_id_from_uri(f) for f in (p.get("filhos") or []) if _id_from_uri(f)]
        return {
            "sku": str(p.get("sku") or "").strip() or f"LI-{p.get('id', '')}",
            "name": str(p.get("nome") or "").strip(),
            "price_cents": 0,
            "stock_qty": 0,
            "available": True,
            "stock_unknown": True,
            "bling_id": "",
            "product_id": str(p.get("id") or ""),
            "variant_id": filhos[0] if filhos else "",
            "url": str(p.get("url") or ""),
            "image_url": "",
            "description": "",
            "variants_count": len(filhos),
            "children_ids": filhos,
            "type": str(p.get("tipo") or "normal"),
        }

    @staticmethod
    def _price_cents(cheio: Any, promo: Any) -> int:
        promo_cents = _cents(promo)
        return promo_cents if promo_cents > 0 else _cents(cheio)

    async def _fill_prices(self, products: list[dict]) -> None:
        """GET /produto_preco/set/{ids} em lotes; preço do pai = preço do primeiro filho."""
        price_ids: dict[str, list[dict]] = {}
        for d in products:
            pid = d.get("variant_id") if d.get("type") == "atributo" and d.get("variant_id") else d.get("product_id")
            if pid:
                price_ids.setdefault(str(pid), []).append(d)
        ids = list(price_ids)
        for i in range(0, len(ids), _PRICE_CHUNK):
            chunk = ids[i:i + _PRICE_CHUNK]
            st, body = await self._request("GET", f"produto_preco/set/{','.join(chunk)}")
            if st != 200:
                log.warning(f"Loja Integrada | preço em lote falhou | http_{st} | ids={len(chunk)}")
                continue
            rows = body.get("objects") if isinstance(body, dict) else body
            rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
            for idx, row in enumerate(rows):
                pid = _id_from_uri(row.get("produto")) or str(row.get("produto_id") or "")
                if not pid and len(rows) == len(chunk):
                    pid = chunk[idx]  # resposta na ordem dos ids (NÃO validado)
                cents = self._price_cents(row.get("cheio", row.get("preco_cheio")), row.get("promocional", row.get("preco_promocional")))
                for d in price_ids.get(pid, []):
                    d["price_cents"] = cents

    async def _enrich(self, result: dict) -> dict:
        """Detalhe (preço, foto, link) + estoque ao vivo do produto casado. Nunca levanta."""
        pid = str(result.get("product_id") or "")
        if not pid:
            return result
        st, body = await self._request("GET", f"produto/{pid}")
        if st == 200 and isinstance(body, dict):
            cents = self._price_cents(body.get("preco_cheio"), body.get("preco_promocional"))
            if cents > 0:
                result["price_cents"] = cents
            img = body.get("imagem_principal") if isinstance(body.get("imagem_principal"), dict) else {}
            result["image_url"] = str(img.get("grande") or img.get("media") or result.get("image_url") or "")
            result["url"] = str(body.get("url") or result.get("url") or "")
            if body.get("descricao_completa"):
                result["description"] = " ".join(re.sub(r"<[^>]+>", " ", str(body.get("descricao_completa"))).split())[:400]
            if not result.get("children_ids"):
                filhos = [_id_from_uri(f) for f in (body.get("filhos") or []) if _id_from_uri(f)]
                result["children_ids"] = filhos
                result["variants_count"] = len(filhos)
                if filhos and not result.get("variant_id"):
                    result["variant_id"] = filhos[0]
        if result.get("type") == "atributo" and result.get("children_ids"):
            stock_ids = list(result["children_ids"])[:_STOCK_CHILDREN]
        else:
            stock_ids = [pid]
        total = 0
        known = False
        exhausted = True
        for sid in stock_ids:
            st, stock = await self._request("GET", f"produto_estoque/{sid}")
            if st != 200 or not isinstance(stock, dict):
                exhausted = False
                continue
            if stock.get("gerenciado") is False:
                exhausted = False  # sem gestão de estoque = disponível
                continue
            known = True
            qty = _qty(stock.get("quantidade_disponivel", stock.get("quantidade")))
            total += max(qty, 0)
            if qty > 0 or _qty(stock.get("situacao_sem_estoque")) != -1:
                exhausted = False
        if known:
            result["stock_qty"] = total
            result["stock_unknown"] = False
            result["available"] = not exhausted
        return result

    # ── contrato ─────────────────────────────────────────────────

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        wanted = max(1, min(int(limit), MAX_SYNC_PRODUCTS))
        cached = _CATALOG_CACHE.get(self._cache_key())
        if cached and time.monotonic() - cached[0] < CATALOG_TTL:
            products = [dict(p) for p in cached[1]][:wanted]
            return {"status": "ok", "products": products, "count": len(products), "cached": True}
        products: list[dict] = []
        offset = 0
        while len(products) < wanted:
            st, body = await self._request(
                "GET", "produto", params={"ativo": "true", "removido": "false", "limit": _PAGE, "offset": offset},
            )
            if st != 200 or not isinstance(body, dict):
                if products:
                    break
                return self._err(st)
            rows = [p for p in (body.get("objects") or []) if isinstance(p, dict)]
            for p in rows:
                if str(p.get("tipo") or "normal") not in _SELLABLE_TYPES:
                    continue
                if p.get("removido") or p.get("bloqueado") or p.get("ativo") is False:
                    continue
                d = self._listing_to_dict(p)
                if d["name"]:
                    products.append(d)
                if len(products) >= wanted:
                    break
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            if not rows or not meta.get("next"):
                break
            offset += _PAGE
        await self._fill_prices(products)
        _CATALOG_CACHE[self._cache_key()] = (time.monotonic(), [dict(p) for p in products])
        # Estoque é desconhecido na listagem: não filtra (a vitrine ficaria vazia).
        return {"status": "ok", "products": products, "count": len(products)}

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        # 1) SKU exato
        # "tem a CAM-PRETA-M?" → o token com pontuação no fim ainda é SKU
        sku_query = re.sub(r"[?!.,;:]+(?=\s|$)", "", query)
        sku_tries = [sku_query] if " " not in sku_query else []
        sku_tries += [t for t in sku_candidates(sku_query) if t not in sku_tries]
        for sku in sku_tries:
            st, body = await self._request("GET", "produto", params={"sku": sku, "limit": 5})
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            rows = [p for p in (body.get("objects") or []) if isinstance(p, dict)] if st == 200 and isinstance(body, dict) else []
            if rows:
                hit = rows[0]
                if str(hit.get("tipo") or "") == "atributo_opcao" and hit.get("pai"):
                    # Variação: o nome/link ficam no pai; preço/estoque na própria variação.
                    result = self._listing_to_dict(hit)
                    result["type"] = "normal"
                else:
                    result = self._listing_to_dict(hit)
                result = await self._enrich(result)
                result["status"] = "found"
                log.info(f"Loja Integrada check_stock | found (sku) | sku={result['sku']} | qty={result['stock_qty']}")
                return result

        # 2) Casa por tokens contra o catálogo (a API não tem busca textual documentada)
        catalog = await self.list_products(limit=MAX_SYNC_PRODUCTS, only_in_stock=False)
        if catalog.get("status") != "ok":
            return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
        match = best_match(query, catalog.get("products") or [])
        if match["status"] == "found":
            result = await self._enrich(dict(match["product"]))
            result["status"] = "found"
            log.info(f"Loja Integrada check_stock | found (match local) | query={query[:40]} | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result
        if match["status"] == "ambiguous":
            return {"status": "ambiguous", "matches": match["matches"]}
        log.info(f"Loja Integrada check_stock | not_found | query={query[:40]} | catalogo={catalog.get('count', 0)}")
        return {"status": "not_found", "query": query}

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """A Loja Integrada não cota frete por API de produto: a política de frete do dono manda."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}

    # ── pedidos, webhooks e cupons ───────────────────────────────

    async def get_order(self, order_id: str) -> dict:
        """Pedido pelo número, já no formato da HUMA. {"status": "ok", "order": {...}} | not_found | erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        st, body = await self._request("GET", f"pedido/{order_id}")
        if st == 200 and isinstance(body, dict) and (body.get("numero") or body.get("id")):
            return {"status": "ok", "order": order_to_huma(body, self.client_id)}
        if st == 404:
            return {"status": "not_found"}
        return self._err(st)

    async def ensure_webhooks(self, url: str, token: str = "") -> dict:
        """
        Cadastra a URL de notificação de pedidos (PUT {raiz}/webhooks/v1/pedido
        {notifyUrl, token}). Não há GET de listagem: o PUT é idempotente por
        desenho da LI ("Registros afetados: 1"). NÃO validado com API real.
        Returns: {"status": "ok", "created": ["pedido"]} ou erro.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        if not url.startswith("https://"):
            return {"status": "error", "detail": "url_must_be_https"}
        st, body = await self._request(
            "PUT", f"{self._root_url()}/webhooks/v1/pedido",
            json_body={"notifyUrl": url, "token": token or ""}, absolute=True,
        )
        if st in (200, 201, 204):
            log.info(f"Loja Integrada webhook de pedido cadastrado | {str(body)[:80] if body else st}")
            return {"status": "ok", "created": ["pedido"], "existing": []}
        log.warning(f"Loja Integrada webhook não cadastrado | http_{st}")
        return {**self._err(st), "created": [], "existing": []}

    async def create_coupon(
        self, code: str, percent: int, hours_valid: int = 48, max_uses: int = 1,
    ) -> dict:
        """Cupom único da conversa (POST /cupom). {"status": "ok", "coupon": {...}} | exists | erro."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        days = max(1, int((int(hours_valid) + 23) // 24))
        validade = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
        payload = {
            "codigo": code,
            "tipo": "porcentagem",
            "valor": f"{int(percent):.2f}",
            "validade": validade,
            "ativo": True,
            "descricao": "Cupom da conversa HUMA IA",
            "condicao_cliente": "todos_clientes",
            "condicao_produto": "todos_produtos",
            "quantidade": int(max_uses),
            "quantidade_por_cliente": 1,
            "cumulativo": False,
            "aplicar_no_total": False,
            "valor_minimo": "0.00",
        }
        st, body = await self._request("POST", "cupom", json_body=payload)
        if st in (200, 201) and isinstance(body, dict):
            return {"status": "ok", "coupon": body}
        if st in (400, 409) and "exist" in str(body or "").lower():
            return {"status": "exists"}
        return self._err(st)
