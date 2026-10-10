# ================================================================
# huma/providers/inventory/omie.py — Adapter Omie ERP (catálogo + estoque)
#
# Implementa InventoryProvider contra a API do Omie
# (https://app.omie.com.br/api/v1). O Omie não tem OAuth: o dono cola
# App Key + App Secret (Omie → Configurações → Aplicativos). Todo
# request é um POST JSON com o envelope
#   {"call": <método>, "app_key": ..., "app_secret": ..., "param": [{...}]}
#
# Métodos usados (conferidos na doc em 2026-10-10):
#   geral/produtos/   ListarProdutos  (pagina, registros_por_pagina, apenas_importado_api, filtrar_apenas_omiepdv)
#                     ConsultarProduto (codigo | codigo_produto)
#   estoque/consulta/ ListarPosEstoque (nPagina, nRegPorPagina, dDataPosicao, cExibeTodos)
#                     PosicaoEstoque   (id_prod, dDataPosicao)
# Erro vem como HTTP 500 com {"faultstring", "faultcode"}; "faultcode"
# de autenticação carrega "SOAP-ENV:Client-" (tratado como unauthorized).
#
# Frete: o Omie não cota frete por API de produto → no_logistics_configured
# (o checkout da HUMA usa a política de frete do dono).
# NÃO validado contra conta real: formato de `imagens` e de
# `PosicaoEstoque`. O adapter nunca levanta.
# ================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("omie")

_DEFAULT_TIMEOUT = 12.0
_PAGE_MAX = 500


def _base_url() -> str:
    from huma.config import OMIE_API_BASE_URL
    return (OMIE_API_BASE_URL or "https://app.omie.com.br/api/v1").rstrip("/")


def _today_br() -> str:
    return datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y")


def _cents(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _qty(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class OmieAdapter(InventoryProvider):
    """Adapter Omie ERP (App Key + App Secret por cliente)."""

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        if identity is not None:
            self.app_key = getattr(identity, "omie_app_key", "") or app_key or ""
            self.app_secret = getattr(identity, "omie_app_secret", "") or app_secret or ""
        else:
            self.app_key = app_key or ""
            self.app_secret = app_secret or ""
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.app_key and self.app_secret)

    # ── HTTP ─────────────────────────────────────────────────────

    async def _call(self, resource: str, method: str, param: dict) -> tuple[int, Any]:
        """
        POST no recurso (ex.: "geral/produtos/") com o envelope do Omie.
        (status, json). 0 = falha de rede. Nunca levanta.
        """
        if not self._has_creds:
            return 401, None
        url = f"{self.base_url}/{resource.strip('/')}/"
        body = {"call": method, "app_key": self.app_key, "app_secret": self.app_secret, "param": [param]}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(url, json=body, headers={"Content-Type": "application/json"})
            try:
                data = resp.json() if resp.content else None
            except ValueError:
                data = None
            if resp.status_code >= 400:
                fault = (data or {}).get("faultstring", "") if isinstance(data, dict) else ""
                log.warning(f"Omie HTTP {resp.status_code} | {method} | {str(fault or resp.text)[:160]}")
            return resp.status_code, data
        except httpx.TimeoutException:
            log.error(f"Timeout | service=omie | {method}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=omie | {method} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _fault(body: Any) -> str:
        return str(body.get("faultstring") or "") if isinstance(body, dict) else ""

    @staticmethod
    def _is_auth_fault(body: Any) -> bool:
        code = str(body.get("faultcode") or "") if isinstance(body, dict) else ""
        text = OmieAdapter._fault(body).lower()
        return "client-" in code.lower() and ("app_key" in text or "app_secret" in text or "chave" in text or "inválid" in text)

    def _err(self, st: int, body: Any) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403) or self._is_auth_fault(body):
            return {"status": "error", "detail": "unauthorized"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta ────────────────────────────────────────────────────

    async def validate_credentials(self) -> dict:
        """
        Testa App Key + App Secret com uma listagem de 1 produto.

        Returns:
            {"status": "ok", "total_products": int} ou
            {"status": "error", "detail", "user_message"} (mensagem em PT).
        """
        if not self._has_creds:
            return {"status": "error", "detail": "no_credentials", "user_message": "Preencha App Key e App Secret."}
        st, body = await self._call("geral/produtos", "ListarProdutos", {
            "pagina": 1, "registros_por_pagina": 1, "apenas_importado_api": "N", "filtrar_apenas_omiepdv": "N",
        })
        if st == 200 and isinstance(body, dict):
            return {"status": "ok", "total_products": _qty(body.get("total_de_registros"))}
        if st == 0:
            return {"status": "error", "detail": "network_error",
                    "user_message": "Não consegui falar com o Omie agora. Tente de novo em instantes."}
        if st in (401, 403) or self._is_auth_fault(body):
            return {"status": "error", "detail": "unauthorized",
                    "user_message": "O Omie não reconheceu essa App Key e App Secret. Confira em Configurações → Aplicativos."}
        fault = self._fault(body)
        # Conta sem produto cadastrado devolve fault "não existem registros": credencial válida.
        if "registros" in fault.lower() and "exist" in fault.lower():
            return {"status": "ok", "total_products": 0}
        return {"status": "error", "detail": f"http_{st}",
                "user_message": f"O Omie respondeu com erro: {fault[:140] or st}"}

    # ── normalização ─────────────────────────────────────────────

    @staticmethod
    def _produto_to_dict(p: dict, stock: dict | None = None) -> dict:
        """Produto do Omie → dict que orchestrator/IA consomem."""
        if not isinstance(p, dict):
            return {}
        imagens = p.get("imagens") or []
        image_url = ""
        if isinstance(imagens, list) and imagens:
            first = imagens[0]
            image_url = str((first.get("url_imagem") if isinstance(first, dict) else first) or "")
        codigo_produto = str(p.get("codigo_produto") or "")
        qty = _qty((stock or {}).get(codigo_produto, 0)) if stock is not None else 0
        return {
            "bling_id": "",
            "omie_id": codigo_produto,
            "sku": str(p.get("codigo") or ""),
            "name": str(p.get("descricao") or "").strip(),
            "price_cents": _cents(p.get("valor_unitario")),
            "stock_qty": qty,
            # Sem leitura de estoque, o produto conta como disponível: quem
            # confirma é o check_stock ao vivo (nunca "esgotado" por falta de dado).
            "available": (qty > 0) if stock is not None else True,
            "stock_unknown": stock is None,
            "description": " ".join(str(p.get("descr_detalhada") or "").split())[:400],
            "url": "",
            "image_url": image_url,
        }

    async def _stock_map(self) -> dict | None:
        """{codigo_produto: saldo} da posição de estoque de hoje. None se não deu pra ler."""
        st, body = await self._call("estoque/consulta", "ListarPosEstoque", {
            "nPagina": 1, "nRegPorPagina": _PAGE_MAX, "dDataPosicao": _today_br(), "cExibeTodos": "S",
            "codigo_local_estoque": 0,
        })
        if st != 200 or not isinstance(body, dict):
            return None
        out: dict = {}
        for item in body.get("produtos") or []:
            if isinstance(item, dict) and item.get("nCodProd") is not None:
                out[str(item.get("nCodProd"))] = _qty(item.get("nSaldo"))
        return out

    async def _stock_for(self, codigo_produto: str) -> int | None:
        st, body = await self._call("estoque/consulta", "PosicaoEstoque", {
            "id_prod": _qty(codigo_produto), "dDataPosicao": _today_br(), "codigo_local_estoque": 0,
        })
        if st == 200 and isinstance(body, dict) and body.get("saldo") is not None:
            return _qty(body.get("saldo"))
        return None

    # ── contrato ─────────────────────────────────────────────────

    async def list_products(self, limit: int = 50, only_in_stock: bool = True) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        capped = max(1, min(int(limit), _PAGE_MAX))
        st, body = await self._call("geral/produtos", "ListarProdutos", {
            "pagina": 1, "registros_por_pagina": capped, "apenas_importado_api": "N", "filtrar_apenas_omiepdv": "N",
        })
        if st != 200 or not isinstance(body, dict):
            fault = self._fault(body)
            if "registros" in fault.lower() and "exist" in fault.lower():
                return {"status": "ok", "products": [], "count": 0}
            return self._err(st, body)
        raw = [p for p in (body.get("produto_servico_cadastro") or []) if isinstance(p, dict)]
        raw = [p for p in raw if str(p.get("inativo") or "N").upper() != "S"]
        stock = await self._stock_map()
        products = [self._produto_to_dict(p, stock) for p in raw]
        products = [p for p in products if p.get("name")]
        if only_in_stock and stock is not None:
            products = [p for p in products if p["available"]]
        return {"status": "ok", "products": products, "count": len(products)}

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        from huma.core.catalog_sync import best_match, sku_candidates

        # 1) SKU exato (a frase inteira ou um token que parece SKU)
        sku_tries = [query] if " " not in query else []
        sku_tries += [t for t in sku_candidates(query) if t not in sku_tries]
        for sku in sku_tries:
            st, body = await self._call("geral/produtos", "ConsultarProduto", {"codigo": sku})
            if st == 200 and isinstance(body, dict) and body.get("codigo_produto"):
                result = self._produto_to_dict(body)
                return await self._with_live_stock(result)
            if st == 0:
                return {"status": "error", "detail": "network_error"}

        # 2) Casa por tokens contra o catálogo (o lead fala do jeito dele)
        catalog = await self.list_products(limit=_PAGE_MAX, only_in_stock=False)
        if catalog.get("status") != "ok":
            return {"status": "error", "detail": catalog.get("detail", "catalog_unavailable")}
        match = best_match(query, catalog.get("products") or [])
        if match["status"] == "found":
            result = dict(match["product"])
            if result.get("stock_unknown"):
                return await self._with_live_stock(result)
            result["status"] = "found"
            log.info(f"Omie check_stock | found | query={query[:40]} | sku={result.get('sku')} | qty={result.get('stock_qty')}")
            return result
        if match["status"] == "ambiguous":
            return {"status": "ambiguous", "matches": match["matches"]}
        return {"status": "not_found", "query": query}

    async def _with_live_stock(self, result: dict) -> dict:
        qty = await self._stock_for(result.get("omie_id", "")) if result.get("omie_id") else None
        if qty is not None:
            result["stock_qty"] = qty
            result["available"] = qty > 0
            result["stock_unknown"] = False
        result["status"] = "found"
        log.info(f"Omie check_stock | found | sku={result.get('sku')} | qty={result.get('stock_qty')} | conhecido={not result.get('stock_unknown')}")
        return result

    async def calc_shipping(self, sku: str, cep_destino: str, qty: int = 1) -> dict:
        """O Omie não cota frete por API de produto: a política de frete do dono manda."""
        if not self._has_creds:
            return {"status": "no_credentials"}
        return {"status": "no_logistics_configured"}
