# ================================================================
# huma/providers/inventory/bling.py — Adapter Bling V3 API
#
# Implementa InventoryProvider contra a API V3 do Bling
# (https://developer.bling.com.br). Cobre estoque + cotação de
# frete numa só integração.
#
# Auth (Fase 2A): access_token global setado em BLING_ACCESS_TOKEN.
# Auth (Fase 2B): OAuth 2.0 por cliente — token vem do ClientIdentity
# e refresh é automático antes de expirar.
#
# Os endpoints do Bling V3 usados aqui são:
#   GET  /produtos                  — busca por nome/SKU
#   GET  /produtos/{id}             — detalhe (peso/dimensões pro frete)
#   GET  /estoques/saldos           — saldo por produto
#   POST /logisticas/cotacoes       — cotação de frete (read-only)
#
# Todos os métodos NUNCA propagam exceção. Falhas de rede/auth viram
# status="error" pra orchestrator decidir como degradar (geralmente:
# "tô confirmando com nosso sistema, te respondo em instantes").
# ================================================================

from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from huma.providers.inventory.base import InventoryProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("bling")

# Timeout default — Bling V3 costuma responder em <1s, 10s dá margem
_DEFAULT_TIMEOUT = 10.0


class BlingAdapter(InventoryProvider):
    """
    Adapter Bling V3.

    Dois modos de uso:
      1. `access_token` direto (Fase 2A — token global ou testes)
      2. `identity` (Fase 2B — token por cliente OAuth, refresh automático)

    Em modo identity, antes de cada request o adapter chama
    `_ensure_fresh_token()`: se o access_token vai expirar em menos
    que BLING_TOKEN_REFRESH_MARGIN_SEC, dispara refresh via /oauth/token
    e persiste os novos tokens no Supabase via db_service.update_client.

    Stateless além do token. Cria um httpx.AsyncClient por chamada
    pra evitar problemas de event loop em fastapi.concurrency. Custo
    de ~5ms por request — negligível dado que cada chamada Bling
    leva 200-800ms.
    """

    def __init__(
        self,
        access_token: str = "",
        identity: "ClientIdentity | None" = None,
        base_url: str = "https://www.bling.com.br/Api/v3",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        """
        Args:
            access_token: Bearer token Bling (modo global / testes).
            identity: ClientIdentity com bling_access_token + refresh
                (modo OAuth por cliente). Se passado, sobrepõe access_token
                e habilita auto-refresh.
            base_url: endpoint base da API V3.
            timeout: timeout em segundos por request.
        """
        self.identity = identity
        if identity is not None:
            self.access_token = (
                getattr(identity, "bling_access_token", "") or access_token
            )
        else:
            self.access_token = access_token or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── Auto-refresh ─────────────────────────────────────────────

    async def _ensure_fresh_token(self) -> None:
        """
        Se em modo identity e access_token expira em breve, faz refresh.

        Atualiza self.access_token + persiste no DB. Erros de refresh
        são logados mas não levantam — request seguinte vai cair em
        401, caller decide como degradar.

        Sem-op quando:
          - modo direct token (sem identity)
          - identity sem refresh_token (não dá pra renovar)
          - expires_at ainda longe da margem de segurança
        """
        if self.identity is None:
            return

        refresh_token = getattr(self.identity, "bling_refresh_token", "") or ""
        if not refresh_token:
            return

        from huma.config import BLING_TOKEN_REFRESH_MARGIN_SEC

        expires = getattr(self.identity, "bling_token_expires_at", None)
        if expires is not None:
            now = datetime.utcnow()
            margin = timedelta(seconds=BLING_TOKEN_REFRESH_MARGIN_SEC)
            if expires > now + margin:
                return  # ainda válido

        # Import tardio pra quebrar ciclo: bling_oauth não importa adapter
        from huma.providers.inventory import bling_oauth

        result = await bling_oauth.refresh_access_token(refresh_token)
        if result.get("status") != "ok":
            log.error(
                f"Bling refresh falhou | client={self.identity.client_id} | "
                f"detail={result.get('detail', '')}"
            )
            return

        new_access = result.get("access_token", "")
        new_refresh = result.get("refresh_token", "") or refresh_token
        new_expires = result.get("expires_at")

        # Atualiza in-memory (vale pra essa instância do adapter)
        self.access_token = new_access
        self.identity.bling_access_token = new_access
        self.identity.bling_refresh_token = new_refresh
        self.identity.bling_token_expires_at = new_expires

        # Persiste no DB pra outras chamadas (workers, processes) pegarem
        try:
            from huma.services import db_service
            await db_service.update_client(self.identity.client_id, {
                "bling_access_token": new_access,
                "bling_refresh_token": new_refresh,
                "bling_token_expires_at": (
                    new_expires.isoformat() if new_expires else None
                ),
            })
            log.info(
                f"Bling tokens refreshed + persisted | client={self.identity.client_id}"
            )
        except Exception as e:
            log.error(
                f"Bling refresh persist falhou | client={self.identity.client_id} | "
                f"{type(e).__name__}: {e}"
            )

    # ── HTTP helper ────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> tuple[int, dict | None]:
        """
        Faz request à API Bling. NUNCA levanta exceção.

        Em modo identity, dispara refresh antes se necessário (no-op em
        modo direct-token ou sem refresh disponível).

        Returns:
            (status_code, parsed_json) — status 0 indica falha de rede.
        """
        await self._ensure_fresh_token()
        if not self.access_token:
            return (0, None)

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(
                    method=method, url=url, params=params,
                    json=json_body, headers=headers,
                )
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                return (resp.status_code, body)
        except httpx.TimeoutException:
            log.error(f"Bling timeout | {method} {path}")
            return (0, None)
        except httpx.HTTPError as e:
            log.error(f"Bling HTTP error | {method} {path} | {type(e).__name__}: {e}")
            return (0, None)
        except Exception as e:
            log.critical(
                f"Bling unexpected | {method} {path} | {type(e).__name__}: {e}"
            )
            return (0, None)

    # ── check_stock ────────────────────────────────────────────

    async def check_stock(self, query_or_sku: str) -> dict:
        if not self.access_token:
            return {"status": "no_credentials"}

        query = (query_or_sku or "").strip()
        if not query:
            return {"status": "not_found", "query": query}

        # Bling V3: filtro por codigo (SKU) ou criterio (busca textual)
        # Tentamos SKU primeiro (match exato é mais confiável); depois texto.
        status_sku, body_sku = await self._request(
            "GET", "/produtos", params={"codigo": query, "limite": 5},
        )
        produtos = self._extract_produtos(body_sku) if status_sku == 200 else []

        if not produtos:
            status_txt, body_txt = await self._request(
                "GET", "/produtos", params={"criterio": query, "limite": 5},
            )
            if status_txt == 200:
                produtos = self._extract_produtos(body_txt)

        if not produtos:
            log.info(f"Bling check_stock | not_found | query={query[:40]}")
            return {"status": "not_found", "query": query}

        if len(produtos) > 1:
            matches = [self._produto_to_dict(p) for p in produtos[:5]]
            log.info(
                f"Bling check_stock | ambiguous | query={query[:40]} | "
                f"matches={len(matches)}"
            )
            return {"status": "ambiguous", "matches": matches}

        result = self._produto_to_dict(produtos[0])
        result["status"] = "found"
        log.info(
            f"Bling check_stock | found | sku={result.get('sku', '?')} | "
            f"qty={result.get('stock_qty', 0)} | price={result.get('price_cents', 0)}"
        )
        return result

    # ── list_products ──────────────────────────────────────────

    async def list_products(
        self, limit: int = 50, only_in_stock: bool = True,
    ) -> dict:
        if not self.access_token:
            return {"status": "no_credentials"}

        # Bling limite máximo por página = 100; clampamos pra evitar 422
        capped = max(1, min(int(limit), 100))
        status, body = await self._request(
            "GET", "/produtos", params={"limite": capped},
        )

        if status == 0:
            return {"status": "error", "detail": "network_error"}
        if status == 401:
            return {"status": "error", "detail": "unauthorized"}
        if status != 200:
            return {"status": "error", "detail": f"http_{status}"}

        produtos = self._extract_produtos(body)
        items = [self._produto_to_dict(p) for p in produtos]
        if only_in_stock:
            items = [p for p in items if p.get("stock_qty", 0) > 0]

        log.info(
            f"Bling list_products | count={len(items)} | only_in_stock={only_in_stock}"
        )
        return {"status": "ok", "products": items, "count": len(items)}

    # ── calc_shipping ──────────────────────────────────────────

    async def calc_shipping(
        self, sku: str, cep_destino: str, qty: int = 1,
    ) -> dict:
        if not self.access_token:
            return {"status": "no_credentials"}

        cep_clean = re.sub(r"\D", "", cep_destino or "")
        if len(cep_clean) != 8:
            return {"status": "invalid_cep", "cep": cep_destino}

        # 1. Localiza o produto pra pegar peso/dimensões + bling_id
        produto = await self.check_stock(sku)
        if produto.get("status") != "found":
            return {"status": "error", "detail": f"product_{produto.get('status')}"}

        # 2. Monta body de cotação. Estrutura segue Bling V3 logística;
        # campos exatos podem precisar ajuste contra conta real (Fase 2B
        # valida end-to-end com conta de teste do dono).
        body = {
            "cepDestino": cep_clean,
            "itens": [{
                "idProduto": produto.get("bling_id"),
                "quantidade": max(1, int(qty)),
            }],
        }
        status, resp = await self._request(
            "POST", "/logisticas/cotacoes", json_body=body,
        )

        if status == 0:
            return {"status": "error", "detail": "network_error"}
        if status == 401:
            return {"status": "error", "detail": "unauthorized"}
        if status == 404:
            # Dono não tem transportadora vinculada no Bling
            return {"status": "no_logistics_configured"}
        if status != 200:
            return {"status": "error", "detail": f"http_{status}"}

        data = (resp or {}).get("data") or {}
        cotacoes = data.get("cotacoes") if isinstance(data, dict) else data
        if not isinstance(cotacoes, list) or not cotacoes:
            return {"status": "no_logistics_configured"}

        # Escolhe a opção mais barata
        def _cost(c: dict) -> float:
            return float(c.get("valor") or c.get("preco") or 1e9)

        cheapest = min(cotacoes, key=_cost)
        cost_cents = int(round(_cost(cheapest) * 100))
        days = int(cheapest.get("prazoEntrega") or cheapest.get("prazo") or 0)
        service = (
            cheapest.get("nome")
            or cheapest.get("transportadora")
            or cheapest.get("servico")
            or "Frete"
        )

        log.info(
            f"Bling calc_shipping | sku={sku} | cep={cep_clean} | qty={qty} | "
            f"cost={cost_cents} | days={days} | service={service}"
        )
        return {
            "status": "ok",
            "cost_cents": cost_cents,
            "days": days,
            "service": service,
            "options": cotacoes,
        }

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_produtos(body: dict | None) -> list[dict]:
        """Bling V3 devolve {data: [...]} pra coleções."""
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    @staticmethod
    def _produto_to_dict(p: dict) -> dict:
        """
        Normaliza produto Bling V3 → dict que orchestrator/IA consomem.

        Campos da resposta V3 (snake-case e camel-case coexistem em
        sub-recursos; tratamos os dois):
          id, codigo, nome, preco, estoque.saldoVirtualTotal, ...
        """
        if not isinstance(p, dict):
            return {}

        estoque = p.get("estoque") or {}
        if not isinstance(estoque, dict):
            estoque = {}

        # Bling expõe saldoVirtualTotal (físico - reservas) — é o que
        # importa pra dizer "tem disponível pra vender"
        qty = (
            estoque.get("saldoVirtualTotal")
            or estoque.get("saldoVirtual")
            or p.get("saldoVirtualTotal")
            or 0
        )
        try:
            qty = int(float(qty))
        except (TypeError, ValueError):
            qty = 0

        preco_raw = p.get("preco") or p.get("precoCusto") or 0
        try:
            price_cents = int(round(float(preco_raw) * 100))
        except (TypeError, ValueError):
            price_cents = 0

        return {
            "bling_id": str(p.get("id") or ""),
            "sku": str(p.get("codigo") or ""),
            "name": str(p.get("nome") or ""),
            "price_cents": price_cents,
            "stock_qty": qty,
            "available": qty > 0,
        }
