# ================================================================
# huma/services/knowledge_service.py — Base de conhecimento v1
#
# Desenho custo-consciente (decisão 2026-08-28 / implementado 2026-09-04):
#   - O documento é processado UMA vez no upload: extrai o texto
#     (PDF/DOCX/TXT/MD/CSV) e pede à IA um resumo estruturado de fatos.
#   - Só o RESUMO (≤ MAX_SUMMARY_CHARS) fica gravado em
#     clients.knowledge_docs e entra no bloco ESTÁTICO do prompt
#     (cacheado) — zero custo extra por mensagem do lead.
#   - Sem busca/embeddings: pra SMB, 8 docs resumidos cabem no prompt.
#
# Nunca guarda o arquivo original (não precisa de Storage).
# ================================================================

import io
import re
import uuid
import zipfile
from datetime import datetime, timezone

import anthropic

from huma.config import AI_MODEL_FAST, ANTHROPIC_API_KEY
from huma.utils.logger import get_logger

log = get_logger("knowledge")

MAX_DOCS = 8
MAX_FILE_BYTES = 10 * 1024 * 1024        # 10MB por arquivo
MAX_SOURCE_CHARS = 60_000                # o que vai pro resumo (≈15k tokens, 1x só)
MAX_SUMMARY_CHARS = 1_500                # o que entra no prompt por documento
MIN_TEXT_CHARS = 40                      # abaixo disso o arquivo é imagem/vazio
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".csv")


def _extension(filename: str) -> str:
    name = (filename or "").lower().strip()
    idx = name.rfind(".")
    return name[idx:] if idx >= 0 else ""


def is_supported(filename: str) -> bool:
    """True quando a extensão do arquivo é uma das aceitas na v1."""
    return _extension(filename) in ALLOWED_EXTENSIONS


def _decode_text(blob: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="ignore")


def _extract_pdf(blob: bytes) -> str:
    from pypdf import PdfReader  # import tardio: dependência só deste fluxo

    reader = PdfReader(io.BytesIO(blob))
    pages: list[str] = []
    for page in reader.pages[:200]:
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:  # página corrompida não derruba o doc inteiro
            log.warning(f"Knowledge | página PDF ilegível | {type(e).__name__}: {e}")
        if sum(len(p) for p in pages) >= MAX_SOURCE_CHARS:
            break
    return "\n".join(pages)


def _extract_docx(blob: bytes) -> str:
    # DOCX = zip com word/document.xml. Parágrafos viram quebras de linha;
    # o resto das tags é descartado. Sem dependência nova.
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&apos;", "'")
    )
    return text


def extract_text(filename: str, blob: bytes) -> str:
    """
    Extrai texto puro do arquivo. Levanta ValueError com mensagem
    amigável (em português) quando o formato não é suportado ou o
    arquivo não tem texto legível (ex.: PDF escaneado sem OCR).
    """
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Formato não suportado. Envie PDF, DOCX, TXT, MD ou CSV.")
    try:
        if ext == ".pdf":
            text = _extract_pdf(blob)
        elif ext == ".docx":
            text = _extract_docx(blob)
        else:
            text = _decode_text(blob)
    except ValueError:
        raise
    except Exception as e:
        log.error(f"Knowledge | extração falhou | ext={ext} | {type(e).__name__}: {e}")
        raise ValueError("Não consegui ler esse arquivo. Ele pode estar corrompido ou protegido.")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError(
            "Esse arquivo não tem texto legível (PDF escaneado ou só imagem). "
            "Exporte como PDF com texto ou envie um DOCX/TXT."
        )
    return text[:MAX_SOURCE_CHARS]


def _summary_prompt(business_name: str, filename: str, text: str) -> str:
    return (
        f"Você organiza a base de conhecimento do negócio \"{business_name or 'do cliente'}\" "
        "pra uma atendente de WhatsApp responder clientes.\n\n"
        f"Documento: {filename}\n"
        "Extraia SÓ os fatos úteis pro atendimento: preços e condições, prazos, regras, "
        "requisitos, passos, horários, políticas (troca, cancelamento, garantia), contatos, "
        "diferenciais. Ignore introduções, juridiquês repetido e formatação.\n\n"
        "Formato: lista simples, uma linha por fato, começando com '- '. Sem markdown, sem títulos, "
        "sem comentários seus. Português do Brasil. Não invente nada que não esteja no texto. "
        f"Máximo {MAX_SUMMARY_CHARS - 200} caracteres — priorize o que um cliente mais perguntaria.\n\n"
        "TEXTO DO DOCUMENTO:\n"
        f"{text}"
    )


async def summarize(client_id: str, business_name: str, filename: str, text: str) -> str:
    """
    Resume o texto em fatos (1 chamada ao modelo rápido, 1x por upload).
    Levanta RuntimeError com mensagem amigável se a IA não estiver disponível.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("A IA não está configurada no servidor (ANTHROPIC_API_KEY).")

    prompt = _summary_prompt(business_name, filename, text)
    try:
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIStatusError as e:
        log.error(f"Knowledge | HTTP {e.status_code} | service=anthropic | client={client_id}")
        raise RuntimeError("A IA está indisponível agora. Tente de novo em instantes.")
    except anthropic.APIConnectionError as e:
        log.error(f"Knowledge | conexão | service=anthropic | client={client_id} | {type(e).__name__}: {e}")
        raise RuntimeError("Não consegui falar com a IA agora. Tente de novo em instantes.")

    summary = "".join(
        getattr(block, "text", "") for block in (response.content or [])
    ).strip()
    summary = summary.replace("```", "").strip()[:MAX_SUMMARY_CHARS]

    # Medição (F6): custo real do processamento, 1x por documento
    try:
        from huma.services.usage_service import log_ai_usage

        usage = getattr(response, "usage", None)
        await log_ai_usage(
            client_id=client_id,
            phone="",
            model=AI_MODEL_FAST,
            tier=0,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            purpose="knowledge",
        )
    except Exception as e:
        log.warning(f"Knowledge | medição falhou | client={client_id} | {type(e).__name__}: {e}")

    return summary


async def process_upload(client_id: str, business_name: str, filename: str, blob: bytes) -> dict:
    """
    Pipeline completo de um upload: valida → extrai → resume → devolve o
    registro pronto pra entrar em clients.knowledge_docs.

    Erros de usuário viram ValueError (400); indisponibilidade da IA
    vira RuntimeError (502). Nunca grava nada — quem persiste é a rota.
    """
    if len(blob) > MAX_FILE_BYTES:
        raise ValueError("Cada arquivo pode ter até 10MB.")
    if not blob:
        raise ValueError("Arquivo vazio.")

    text = extract_text(filename, blob)
    summary = await summarize(client_id, business_name, filename, text)
    if not summary:
        raise RuntimeError("A IA não conseguiu resumir esse documento. Tente outro formato.")

    doc = {
        "id": uuid.uuid4().hex[:12],
        "name": (filename or "documento").strip()[:120],
        "size": len(blob),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
        "summary": summary,
        "chars": len(text),
    }
    log.info(
        f"Knowledge | doc processado | client={client_id} | name={doc['name']} | "
        f"size={doc['size']} | chars={doc['chars']} | summary_chars={len(summary)}"
    )
    return doc


def public_doc(doc: dict) -> dict:
    """Projeção do documento pro Cockpit (sem o resumo inteiro no listing)."""
    summary = str(doc.get("summary") or "")
    return {
        "id": doc.get("id", ""),
        "name": doc.get("name", ""),
        "size": int(doc.get("size") or 0),
        "uploaded_at": doc.get("uploaded_at", ""),
        "status": doc.get("status", "ready"),
        "facts": summary.count("\n") + 1 if summary else 0,
        "summary": summary,
    }
