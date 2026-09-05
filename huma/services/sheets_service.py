# ================================================================
# huma/services/sheets_service.py — Planilha de leads no Google do dono
#
# "Cada lead que a HUMA qualifica cai numa planilha sua." É o CRM da
# maioria dos donos de negócio. A planilha é criada UMA vez (na conexão
# com Google) no Drive do dono e cada evento de lead vira uma linha
# (ver lead_events.py).
#
# Só usa a API REST do Sheets via httpx (sem client library extra).
# Nunca levanta exceção pro caller: devolve dict com status.
# ================================================================

from __future__ import annotations

import httpx

from huma.services import google_oauth
from huma.utils.logger import get_logger

log = get_logger("sheets")

SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
_HTTP_TIMEOUT = 12.0
SHEET_TAB = "Leads"

# Cabeçalho fixo — a ordem é o contrato do build_row (lead_events).
HEADER = [
    "Data/hora",
    "Evento",
    "Nome",
    "Telefone / ID",
    "E-mail",
    "Origem",
    "Canal",
    "Estágio",
    "Serviço / produto",
    "Valor (R$)",
    "Quando",
    "Resumo",
    "Fatos do lead",
    "Conversa",
]


async def create_leads_sheet(refresh_token: str, business_name: str) -> dict:
    """
    Cria a planilha "HUMA — Leads de {negócio}" no Drive do dono com a
    aba Leads e o cabeçalho pronto.

    Returns:
        {"status": "ok", "sheet_id": str, "sheet_url": str}
        {"status": "error", "detail": str}
    """
    access = await google_oauth.fetch_access_token(refresh_token)
    if not access:
        return {"status": "error", "detail": "no_access_token"}

    title = f"HUMA — Leads de {business_name}".strip() if business_name else "HUMA — Leads"
    body = {
        "properties": {"title": title, "locale": "pt_BR", "timeZone": "America/Sao_Paulo"},
        "sheets": [
            {
                "properties": {
                    "title": SHEET_TAB,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "data": [
                    {
                        "startRow": 0,
                        "startColumn": 0,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": h},
                                        "userEnteredFormat": {"textFormat": {"bold": True}},
                                    }
                                    for h in HEADER
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                SHEETS_URL, json=body, headers={"Authorization": f"Bearer {access}"},
            )
        if resp.status_code not in (200, 201):
            log.error(f"Sheets create falhou | status={resp.status_code} | {resp.text[:200]}")
            return {"status": "error", "detail": f"http_{resp.status_code}"}
        data = resp.json()
        sheet_id = data.get("spreadsheetId") or ""
        url = data.get("spreadsheetUrl") or (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}" if sheet_id else ""
        )
        log.info(f"Sheets | planilha criada | id={sheet_id}")
        return {"status": "ok", "sheet_id": sheet_id, "sheet_url": url}
    except httpx.TimeoutException:
        log.error("Timeout | service=sheets | op=create")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=sheets | op=create | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}


async def append_row(refresh_token: str, sheet_id: str, row: list) -> dict:
    """
    Acrescenta uma linha na aba Leads.

    Returns:
        {"status": "ok"} | {"status": "error", "detail": str}
    """
    if not sheet_id:
        return {"status": "error", "detail": "no_sheet"}
    access = await google_oauth.fetch_access_token(refresh_token)
    if not access:
        return {"status": "error", "detail": "no_access_token"}

    url = (
        f"{SHEETS_URL}/{sheet_id}/values/{SHEET_TAB}!A1:append"
        f"?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.post(
                url,
                json={"values": [[("" if v is None else str(v)) for v in row]]},
                headers={"Authorization": f"Bearer {access}"},
            )
        if resp.status_code != 200:
            log.error(f"Sheets append falhou | sheet={sheet_id} | status={resp.status_code} | {resp.text[:200]}")
            return {"status": "error", "detail": f"http_{resp.status_code}"}
        return {"status": "ok"}
    except httpx.TimeoutException:
        log.error(f"Timeout | service=sheets | op=append | sheet={sheet_id}")
        return {"status": "error", "detail": "timeout"}
    except httpx.HTTPError as e:
        log.error(f"HTTP erro | service=sheets | op=append | sheet={sheet_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": f"http_error_{type(e).__name__}"}
