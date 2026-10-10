# ================================================================
# huma/providers/inventory/tiny.py — Adapter Tiny ERP / Olist (catálogo + estoque)
#
# Implementa InventoryProvider contra a API v2 do Tiny
# (https://api.tiny.com.br/api2). Sem OAuth: o dono gera o token em
# Tiny → Configurações → Usuários e API → Gerar token. Todo request é
# um POST form-urlencoded com `token` e `formato=json`.
#
# Endpoints usados (conferidos na doc em 2026-10-10):
#   info.php                   — valida o token (retorno.conta)
#   produtos.pesquisa.php      — busca por nome/código (pesquisa, situacao=A, pagina)
#   produto.obter.php          — detalhe (descricao_complementar, anexos[].anexo)
#   produto.obter.estoque.php  — saldo (retorno.produto.saldo)   [NÃO validado]
# Erro vem com HTTP 200 e retorno.status="Erro" + erros[].erro;
# codigo_erro 2/3 = token inválido.
#
# Estoque na listagem: o Tiny não devolve saldo na pesquisa, então
# list_products marca stock_unknown=True (disponível até prova em
# contrário) e check_stock consulta o saldo ao vivo do produto casado.
# Frete: não cota por API de produto → no_logistics_configured.
# ================================================================

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("tiny")

_DEFAULT_TIMEOUT = 12.0
_AUTH_ERROR_CODES = {"2", "3"}


def _base_url() -> str:
    from huma.config import TINY_API_BASE_URL
    return (TINY_API_BASE_URL or "https://api.tiny.com.br/api2").rstrip("/")


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


class TinyAdapter(InventoryProvider):
    """Adapter Tiny ERP v2 (token por cliente)."""

    def __init__(
        self,
        api_token: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        if identity is not None:
            self.api_token = getattr(identity, "tiny_api_token", "") or api_token or ""
        else:
            self.api_token = api_token or ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.api_token)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _call(self, endpoint: str, **fields: Any) -> tuple[int, Any]:
        """
        POST form no endpoint (ex.: "produtos.pesquisa.php").
        (status, json). 0 = falha de rede. Nunca levanta.
        """
        if not self._has_creds:
            return 401, None
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = {"token": self.api_token, "formato": "json"}
        data.update({k: v for k, v in fields.items() if v is not None})
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(url, data=data, headers={"Accept": "application/json"})
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 400:
                log.warning(f"Tiny HTTP {resp.status_code} | {endpoint} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=tiny | {endpoint}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=tiny | {endpoint} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _retorno(body: Any) -> dict:
        return body.get("retorno") if isinstance(body, dict) and isinstance(body.get("retorno"), dict) else {}

    @staticmethod
    def _erro(ret: dict) -> tuple[str, str]:
        """(codigo_erro, primeira mensagem) de um retorno com status Erro."""
        code = str(ret.get("codigo_erro") or "")
        erros = ret.get("erros") or []
        msg = ""
        if isinstance(erros, list) and erros:
            first = erros[0]
            msg = str(first.get("erro") if isinstance(first, dict) else first)
        return code, msg

    def _err(self, st: int, body: Any) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        ret = self._retorno(body)
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if ret.get("status") == "Erro":
            code, _ = self._erro(ret)
            if code in _AUTH_ERROR_CODES:
                return {"status": "error", "detail": "unauthorized"}
            return {"status": "error", "detail": f"tiny_{code or 'erro'}"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def validate_token(self) -> dict:
        """
        Valida o token com info.php.

        Returns:
            {"status": "ok", "account_name"} ou
            {"status": "error", "detail", "user_message"} (mensagem em PT).
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Cole o token da API do Tiny."}
        st, body = await self._call("info.php")
        ret = self._retorno(body)
        if st == 200 and ret.get("status") == "OK":
            conta = ret.get("conta") or {}
            name = str(conta.get("fantasia") or conta.get("razao_social") or "") if isinstance(conta, dict) else ""
            return {"status": "ok", "account_name": name}
        if st == 0:
            return {"status": "error", "detail": "network_error",
                    "user_message": "Não consegui falar com o Tiny agora. Tente de novo em instantes."}
        code, msg = self._erro(ret)
        if code in _AUTH_ERROR_CODES or st in (401, 403):
            return {"status": "error", "detail": "unauthorized",
                    "user_message": "O Tiny não reconheceu esse token. Confira em Configurações → Usuários e API."}
        return {"status": "error", "detail": f"tiny_{code or st}",
                "user_message": f"O Tiny respondeu com erro: {msg[:140] or st}"}

    # ── normalização ─────────────────────────────────────────────

    @staticmethod
    def _produto_to_dict(p: dict) -> dict:
        """Produto da pesquisa do Tiny → dict que orchestrator/IA consomem (estoque desconhecido)."""
        if not isinstance(p, dict):
            return {}
        preco = p.get("preco_promocional")
        try:
            promo = float(str(preco or 0).replace(",", "."))
        except (TypeError, ValueError):
            promo = 0.0
        price_cents = _cents(preco) if promo > 0 else _cents(p.get("preco"))
        return {
            "bling_id": "",
            "tiny_id": str(p.get("id") or ""),
            "sku": str(p.get("codigo") or ""),
            "name": str(p.get("nome") or "").strip(),
            "price_cents": price_cents,
            "stock_qty": 0,
            "available": True,
            "stock_unknown": True,
            "description": "",
            "url": "",
            "image_url": "",
        }

    async def _search(self, pesquisa: str, limit: int) -> tuple[int, Any, list[dict]]:
        st, body = await self._call("produtos.pesquisa.php", pesquisa=pesquisa, situacao="A", pagina=1)
        ret = self._retorno(body)
        if st != 200 or ret.get("status") != "OK":
            return st, body, []
        out: list[dict] = []
        for item in ret.get("produtos") or []:
            p = item.get("produto") if isinstance(item, dict) else None
            if not isinstance(p, dict):
                continue
            # Produto "pai" de variação não se vende; a variação sim.
            if str(p.get("tipoVariacao") or "N").upper() == "P":
                continue
            d = self._produto_to_dict(p)
            if d.get("name"):
                out.append(d)
            if len(out) >= limit:
                break
        return st, body, out

    async def _enrich(self, result: dict) -> dict:
        """Saldo ao vivo + foto/descrição do produto casado. Nunca levanta."""
        tiny_id = result.get("tiny_id") or ""
        if not tiny_id:
            return result
        st, body = await self._call("produto.obter.estoque.php", id=tiny_id)
        ret = self._retorno(body)
        prod = ret.get("produto") if st == 200 and ret.get("status") == "OK" else None
        if isinstance(prod, dict) and prod.get("saldo") is not None:
            qty = _qty(prod.get("saldo"))
            result["stock_qty"] = qty
            result["available"] = qty > 0
            result["stock_unknown"] = False
        st, body = await self._call("produto.obter.php", id=tiny_id)
        ret = self._retorno(body)
        prod = ret.get("produto") if st == 200 and ret.get("status") == "OK" else None
        if isinstance(prod, dict):
            result["description"] = " ".join(str(prod.get("descricao_complementar") or "").split())[:400]
            anexos = prod.get("anexos") or []
            if isinstance(anexos, list) and anexos:
                first = anexos[0]
                result["image_url"] = str((first.get("anexo") if isinstance(first, dict) else first) or "")
            if not result["image_url"]:
                externas = prod.get("imagens_externas") or []
                if isinstance(externas, list) and externas and isinstance(externas[0], dict):
                    img = externas[0].get("imagem_externa") or {}
                    result["image_url"] = str(img.get("url") or "") if isinstance(img, dict) else ""
        return result

    # ── contrato ─────────────────────────────────────────────────

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        capped = max(1, min(int(limit), 100))
        st, body, products = await self._search("", capped)
        ret = self._retorno(body)
        if st != 200 or ret.get("status") != "OK":
            code, msg = self._erro(ret)
            # "Nenhum registro" não é erro: catálogo vazio.
            if code in ("20",) or "nenhum" in msg.lower():
                return {"status": "ok", "products": [], "count": 0}
            return self._err(st, body)
        # Estoque é desconhecido na listagem (stock_unknown=True): não
        # filtra, senão a vitrine ficaria vazia. O check_stock confirma.
        return {"status": "ok", "products": products, "count": len(products)}

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        st, body, products = await self._search(query, 20)
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        ret = self._retorno(body)
        if st != 200 or ret.get("status") != "OK":
            code, msg = self._erro(ret)
            if not (code in ("20",) or "nenhum" in msg.lower()):
                return self._err(st, body)
            products = []

        # 1) SKU exato dentro do que a busca trouxe
        sku_tries = [query] if " " not in query else []
        sku_tries += [t for t in sku_candidates(query) if t not in sku_tries]
        for sku in sku_tries:
            for p in products:
                if p.get("sku", "").lower() == sku.lower():
                    result = await self._enrich(dict(p))
                    result["status"] = "found"
                    log.info(f"Tiny check_stock | found (sku) | sku={result['sku']} | qty={result['stock_qty']}")
                    return result
        if len(products) == 1:
            result = await self._enrich(dict(products[0]))
            result["status"] = "found"
            log.info(f"Tiny check_stock | found | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result

        # 2) Casa por tokens (a busca do Tiny é por nome; o lead fala do jeito dele)
        pool = products
        if not pool:
            catalog = await self.list_products(limit=100, only_in_stock=False)
            if catalog.get("status") != "ok":
                return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
            pool = catalog.get("products") or []
        match = best_match(query, pool)
        if match["status"] == "found":
            result = await self._enrich(dict(match["product"]))
            result["status"] = "found"
            log.info(f"Tiny check_stock | found (match local) | query={query[:40]} | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result
        if match["status"] == "ambiguous":
            return {"status": "ambiguous", "matches": match["matches"]}
        return {"status": "not_found", "query": query}

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """O Tiny não cota frete por API de produto: a política de frete do dono manda."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}
