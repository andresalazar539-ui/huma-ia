# ================================================================
# huma/core/contact_import.py — Importação da lista de contatos
#
# Módulo PURO (stdlib + openpyxl pra .xlsx). Recebe o arquivo que o
# dono tem (exportado do Excel, do Google, de um sistema antigo) e
# devolve contatos limpos + o motivo de cada linha que ficou de fora.
#
# O dono não é técnico, então a planilha vem de todo jeito:
#   - separador vírgula, ponto e vírgula ou tab
#   - acento quebrado (arquivo do Excel em Latin-1)
#   - telefone com parêntese, traço, espaço, +55, zero na frente
#   - telefone sem DDD, fixo, repetido
#   - coluna de telefone chamada "celular", "whats", "fone", "contato"
#   - sem linha de cabeçalho
#
# Nada aqui toca banco: cruzar com quem já é lead, cliente ou pediu pra
# parar é trabalho do reactivation_engine.
# ================================================================

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_ROWS = 10000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXTRA_COLUMNS = 6
MAX_VALUE_CHARS = 120

REASON_DUPLICATE = "repetido"
REASON_NO_DDD = "sem_ddd"
REASON_INVALID = "numero_invalido"
REASON_LANDLINE = "fixo"
REASON_EMPTY = "sem_numero"
REASON_FOREIGN = "fora_do_brasil"

REASON_LABELS: dict[str, str] = {
    REASON_DUPLICATE: "Número repetido na planilha",
    REASON_NO_DDD: "Número sem DDD",
    REASON_INVALID: "Número inválido",
    REASON_LANDLINE: "Telefone fixo (não tem WhatsApp)",
    REASON_EMPTY: "Linha sem número",
    REASON_FOREIGN: "Número de fora do Brasil",
    # preenchidos pelo motor, depois de cruzar com o banco
    "pediu_pra_parar": "Pediu pra não receber mensagem",
    "ja_em_conversa": "Já está em conversa com você",
    "com_humano": "Está sendo atendido por alguém da equipe",
    "ja_e_cliente": "Já é cliente",
    "numero_da_conta": "É um número da sua própria conta",
    "limite_do_teste": "Passou do limite do teste grátis",
}

_PHONE_HEADERS = ("telefone", "celular", "whatsapp", "whats", "zap", "fone", "phone", "numero", "contato", "tel", "mobile")
_NAME_HEADERS = ("nome", "name", "cliente", "contato", "razao", "paciente", "aluno", "lead")
_OWNER_HEADERS = ("vendedor", "atendente", "responsavel", "corretor", "consultor", "dono")

# DDDs que existem no Brasil.
VALID_DDDS: frozenset[str] = frozenset({
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22", "24", "27", "28",
    "31", "32", "33", "34", "35", "37", "38", "41", "42", "43", "44", "45", "46", "47",
    "48", "49", "51", "53", "54", "55", "61", "62", "63", "64", "65", "66", "67", "68",
    "69", "71", "73", "74", "75", "77", "79", "81", "82", "83", "84", "85", "86", "87",
    "88", "89", "91", "92", "93", "94", "95", "96", "97", "98", "99",
})


@dataclass
class Contact:
    """Um contato pronto pra entrar na reativação."""
    phone: str
    name: str = ""
    extra: dict = field(default_factory=dict)
    owner: str = ""
    line: int = 0


@dataclass
class Rejected:
    """Uma linha que ficou de fora, com o motivo."""
    line: int
    raw: str
    name: str
    reason: str


@dataclass
class ImportResult:
    contacts: list[Contact] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    total_rows: int = 0
    truncated: bool = False
    phone_column: str = ""
    name_column: str = ""
    owner_column: str = ""
    error: str = ""

    def summary(self) -> dict:
        """Números da importação pra tela (contagem por motivo)."""
        by_reason: dict[str, int] = {}
        for r in self.rejected:
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        return {
            "linhas": self.total_rows,
            "prontos": len(self.contacts),
            "fora": len(self.rejected),
            "por_motivo": by_reason,
            "colunas": list(self.columns),
            "cortada": self.truncated,
        }


def _fold(text: Any) -> str:
    base = unicodedata.normalize("NFKD", str(text or "").lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(base.split())


def _clean(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text[:MAX_VALUE_CHARS]


def _decode(data: bytes) -> str:
    """Bytes → texto. Tenta UTF-8 (com e sem BOM) e cai pra Latin-1 (Excel)."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    first = [line for line in sample.splitlines() if line.strip()][:5]
    counts = {d: sum(line.count(d) for line in first) for d in (";", ",", "\t", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def normalize_phone(raw: Any, default_ddd: str = "") -> tuple[str, str]:
    """
    Telefone do jeito que veio → (número pronto, motivo de recusa).

    Número pronto é sempre "55" + DDD + 9 dígitos (celular). Celular
    antigo com 8 dígitos ganha o 9 na frente.

    Args:
        raw: texto da célula.
        default_ddd: DDD pra completar número que veio sem ("" = não completa).

    Returns:
        (phone, "") quando deu certo; ("", motivo) quando não deu.
    """
    text = str(raw if raw is not None else "").strip()
    # Planilha costuma trazer número como 5.511988887777E12 ou "11988887777.0"
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    digits = re.sub(r"\D", "", text)
    if not digits:
        return "", REASON_EMPTY

    if text.startswith("+") and not digits.startswith("55"):
        return "", REASON_FOREIGN
    digits = digits.lstrip("0")
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]

    if len(digits) in (8, 9):
        ddd = re.sub(r"\D", "", str(default_ddd or ""))
        if ddd not in VALID_DDDS:
            return "", REASON_NO_DDD
        digits = ddd + digits

    if len(digits) not in (10, 11):
        return "", REASON_INVALID
    ddd, local = digits[:2], digits[2:]
    if ddd not in VALID_DDDS:
        return "", REASON_INVALID
    if len(local) == 9:
        if local[0] != "9":
            return "", REASON_INVALID
    else:
        # 8 dígitos: fixo começa de 2 a 5; celular antigo de 6 a 9
        if local[0] in "2345":
            return "", REASON_LANDLINE
        local = "9" + local
    if len(set(local)) == 1:
        return "", REASON_INVALID
    return "55" + ddd + local, ""


def _looks_like_phone(value: Any) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    return 8 <= len(digits) <= 13


def _pick_column(headers: list[str], wanted: tuple[str, ...], taken: set[int]) -> int:
    folded = [_fold(h) for h in headers]
    for key in wanted:
        for i, h in enumerate(folded):
            if i not in taken and (h == key or h.startswith(key + " ") or key in h.split()):
                return i
    for key in wanted:
        for i, h in enumerate(folded):
            if i not in taken and key in h:
                return i
    return -1


def _rows_from_csv(data: bytes) -> list[list[str]]:
    text = _decode(data)
    delimiter = _sniff_delimiter(text[:4000])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[_clean(c) for c in row] for row in reader]


def _rows_from_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    rows: list[list[str]] = []
    for row in sheet.iter_rows(values_only=True):
        cells = []
        for cell in row:
            if isinstance(cell, float) and cell.is_integer():
                cell = int(cell)
            cells.append(_clean(cell))
        rows.append(cells)
        if len(rows) > MAX_ROWS + 50:
            break
    book.close()
    return rows


def _rows_from_text(text: str) -> list[list[str]]:
    """Lista colada na tela: um contato por linha, "telefone, nome" ou "nome; telefone"."""
    return _rows_from_csv((text or "").encode("utf-8"))


def parse_contacts(
    data: bytes | str,
    filename: str = "",
    default_ddd: str = "",
) -> ImportResult:
    """
    Lê a planilha (ou a lista colada) e devolve contatos limpos.

    Args:
        data: bytes do arquivo (.csv, .txt, .xlsx) ou texto colado.
        filename: nome do arquivo (decide CSV ou Excel).
        default_ddd: DDD pra completar número sem DDD.

    Returns:
        ImportResult. Em arquivo ilegível, `error` vem preenchido em
        português e as listas vêm vazias. Nunca levanta.
    """
    result = ImportResult()
    try:
        if isinstance(data, str):
            rows = _rows_from_text(data)
        else:
            if len(data) > MAX_FILE_BYTES:
                result.error = "O arquivo passa de 5 MB. Divida a planilha em partes menores."
                return result
            name = (filename or "").lower()
            if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
                rows = _rows_from_xlsx(data)
            elif name.endswith(".xls"):
                result.error = "Esse é o formato antigo do Excel. Abra a planilha e salve como .xlsx ou .csv."
                return result
            else:
                rows = _rows_from_csv(data)
    except Exception:
        result.error = "Não consegui ler esse arquivo. Salve a planilha como .csv ou .xlsx e tente de novo."
        return result

    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        result.error = "A planilha está vazia."
        return result

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    # Tem cabeçalho? Se a primeira linha não tem nenhum telefone, é cabeçalho.
    first = rows[0]
    has_header = not any(_looks_like_phone(c) for c in first)
    if has_header:
        headers = [c or f"Coluna {i + 1}" for i, c in enumerate(first)]
        body = rows[1:]
    else:
        headers = [f"Coluna {i + 1}" for i in range(width)]
        body = rows

    phone_idx = _pick_column(headers, _PHONE_HEADERS, set()) if has_header else -1
    if phone_idx < 0 or not any(_looks_like_phone(r[phone_idx]) for r in body[:50]):
        # Acha a coluna que mais parece telefone. Se nenhuma parece e o
        # cabeçalho disse qual é, vale o cabeçalho (os números é que estão ruins).
        scores = [sum(1 for r in body[:200] if _looks_like_phone(r[i])) for i in range(width)]
        if scores and max(scores) > 0:
            phone_idx = scores.index(max(scores))
    if phone_idx < 0:
        result.error = "Não achei a coluna de telefone. Confira se a planilha tem uma coluna com os números."
        return result

    taken = {phone_idx}
    name_idx = _pick_column(headers, _NAME_HEADERS, taken) if has_header else -1
    if name_idx < 0:
        # Primeira coluna de texto que não é telefone nem e-mail.
        for i in range(width):
            if i in taken:
                continue
            sample = [r[i] for r in body[:50] if r[i]]
            if sample and sum(1 for v in sample if re.search(r"[A-Za-zÀ-ÿ]", v) and "@" not in v) >= len(sample) * 0.6:
                name_idx = i
                break
    if name_idx >= 0:
        taken.add(name_idx)
    owner_idx = _pick_column(headers, _OWNER_HEADERS, taken) if has_header else -1
    if owner_idx >= 0:
        taken.add(owner_idx)

    extra_idx = [i for i in range(width) if i not in taken and has_header][:MAX_EXTRA_COLUMNS]

    result.columns = [headers[i] for i in extra_idx]
    result.phone_column = headers[phone_idx]
    result.name_column = headers[name_idx] if name_idx >= 0 else ""
    result.owner_column = headers[owner_idx] if owner_idx >= 0 else ""
    result.total_rows = len(body)
    if len(body) > MAX_ROWS:
        body = body[:MAX_ROWS]
        result.truncated = True

    seen: set[str] = set()
    offset = 2 if has_header else 1
    for i, row in enumerate(body):
        line = i + offset
        raw_phone = row[phone_idx]
        name = row[name_idx] if name_idx >= 0 else ""
        # Nome que é só número ou e-mail não serve pra cumprimentar.
        if name and (not re.search(r"[A-Za-zÀ-ÿ]", name) or "@" in name):
            name = ""
        phone, reason = normalize_phone(raw_phone, default_ddd)
        if reason:
            result.rejected.append(Rejected(line=line, raw=raw_phone, name=name, reason=reason))
            continue
        if phone in seen:
            result.rejected.append(Rejected(line=line, raw=raw_phone, name=name, reason=REASON_DUPLICATE))
            continue
        seen.add(phone)
        extra = {headers[j]: row[j] for j in extra_idx if row[j]}
        result.contacts.append(Contact(
            phone=phone, name=name, extra=extra,
            owner=row[owner_idx] if owner_idx >= 0 else "", line=line,
        ))
    return result


def first_name(name: str) -> str:
    """Primeiro nome com a inicial maiúscula ('MARIA DA SILVA' → 'Maria'). '' se não der."""
    parts = [p for p in re.split(r"\s+", str(name or "").strip()) if p]
    if not parts:
        return ""
    first = re.sub(r"[^A-Za-zÀ-ÿ'-]", "", parts[0])
    if len(first) < 2:
        return ""
    return first[0].upper() + first[1:].lower()


def rejected_to_csv(rejected: list[Rejected]) -> str:
    """Planilha das linhas que ficaram de fora, pro dono baixar e corrigir."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["linha", "telefone", "nome", "motivo"])
    for r in rejected:
        writer.writerow([r.line, r.raw, r.name, REASON_LABELS.get(r.reason, r.reason)])
    return out.getvalue()
