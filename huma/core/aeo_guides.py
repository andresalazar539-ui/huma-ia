# ================================================================
# huma/core/aeo_guides.py: Guias citáveis em arquivo (2026-11-07)
#
# Cada guia é UM arquivo Markdown em huma/content/guias/<tipo>/<slug>.md
# e vira a página /para/<tipo>/<slug> sozinho: entra no sitemap, no
# llms.txt, no llms-full.txt, no IndexNow e na página do tipo de negócio.
# Escrever página nova = criar o arquivo. Nada de código.
#
# Formato (ver huma/content/guias/LEIA-ME.md):
#   ---
#   titulo: Como colocar IA no WhatsApp da clínica
#   pergunta: Como colocar IA no WhatsApp da clínica?
#   descricao: frase de até 160 caracteres pro Google
#   atualizado: AAAA-MM-DD
#   ---
#   Resposta direta em 2 ou 3 frases (primeiro parágrafo).
#
#   ## Seção
#   Parágrafo, lista com "- ", passos com "1. ", **negrito**,
#   link interno [texto](/precos).
#
#   ## Perguntas frequentes
#   ### Pergunta?
#   Resposta.
#
# `problems(guide)` é a régua de boa prática; o teste test_aeo_guides.py
# roda em TODOS os arquivos e barra o push de página fora do padrão.
# Módulo puro (só stdlib).
# ================================================================
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content" / "guias"
FAQ_HEADING = "perguntas frequentes"

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((/[^)\s]*)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


@dataclass
class Guide:
    """Um guia lido do disco."""

    vertical: str
    slug: str
    titulo: str = ""
    pergunta: str = ""
    descricao: str = ""
    atualizado: str = ""
    answer: str = ""
    blocks: list[tuple[str, object]] = field(default_factory=list)  # (tipo, conteúdo)
    faq: list[tuple[str, str]] = field(default_factory=list)
    source: str = ""

    @property
    def path(self) -> str:
        return f"/para/{self.vertical}/{self.slug}"


def _inline(text: str) -> str:
    """Escapa HTML e aplica **negrito** e link interno [t](/caminho)."""
    out = html.escape(text, quote=True)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    return _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)


def _plain(text: str) -> str:
    """Texto sem marcação (pro JSON-LD e pro llms-full)."""
    text = _BOLD_RE.sub(r"\1", text)
    return _LINK_RE.sub(r"\1", text)


def parse(raw: str, vertical: str, slug: str) -> Guide:
    """Lê o Markdown de um guia (frontmatter + corpo) para um Guide."""
    g = Guide(vertical=vertical, slug=slug, source=raw)
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for line in raw[3:end].strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    if key in ("titulo", "pergunta", "descricao", "atualizado"):
                        setattr(g, key, value.strip())
            body = raw[end + 4:]

    in_faq = False
    pending_q = ""
    paragraph: list[str] = []
    list_items: list[str] = []
    list_kind = ""

    def flush_paragraph() -> None:
        nonlocal paragraph, pending_q
        if not paragraph:
            return
        text = " ".join(paragraph).strip()
        paragraph = []
        if in_faq and pending_q:
            g.faq.append((pending_q, text))
            pending_q = ""
        elif not g.answer and not g.blocks:
            g.answer = text
        else:
            g.blocks.append(("p", text))

    def flush_list() -> None:
        nonlocal list_items, list_kind
        if list_items:
            g.blocks.append((list_kind, list_items))
        list_items, list_kind = [], ""

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            heading = stripped[3:].strip()
            in_faq = heading.lower() == FAQ_HEADING
            if not in_faq:
                g.blocks.append(("h2", heading))
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            if in_faq:
                pending_q = stripped[4:].strip()
            else:
                g.blocks.append(("h3", stripped[4:].strip()))
            continue
        m_ol = re.match(r"^\d+\.\s+(.*)$", stripped)
        if stripped.startswith("- ") or m_ol:
            flush_paragraph()
            kind = "ol" if m_ol else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(m_ol.group(1) if m_ol else stripped[2:].strip())
            continue
        flush_list()
        paragraph.append(stripped)
    flush_paragraph()
    flush_list()
    return g


def body_html(g: Guide) -> str:
    """Corpo do guia em HTML (sem a resposta direta e sem o FAQ)."""
    parts: list[str] = []
    for kind, content in g.blocks:
        if kind in ("h2", "h3"):
            parts.append(f"<{kind}>{_inline(str(content))}</{kind}>")
        elif kind == "p":
            parts.append(f"<p>{_inline(str(content))}</p>")
        elif kind in ("ul", "ol"):
            items = "".join(f"<li>{_inline(i)}</li>" for i in content)  # type: ignore[union-attr]
            parts.append(f"<{kind}>{items}</{kind}>")
    return "\n".join(parts)


def body_markdown(g: Guide) -> str:
    """Corpo do guia em Markdown limpo (pro llms-full.txt)."""
    lines: list[str] = []
    for kind, content in g.blocks:
        if kind == "h2":
            lines += [f"### {_plain(str(content))}", ""]
        elif kind == "h3":
            lines += [f"#### {_plain(str(content))}", ""]
        elif kind == "p":
            lines += [_plain(str(content)), ""]
        elif kind == "ul":
            lines += [f"- {_plain(i)}" for i in content] + [""]  # type: ignore[union-attr]
        elif kind == "ol":
            lines += [f"{n}. {_plain(i)}" for n, i in enumerate(content, 1)] + [""]  # type: ignore[arg-type]
    return "\n".join(lines)


def plain(text: str) -> str:
    """Remove a marcação de um trecho (pública pro renderizador)."""
    return _plain(text)


def load_all(base: Path | None = None) -> list[Guide]:
    """Todos os guias do disco, ordenados por tipo e slug. Ignora LEIA-ME e arquivos fora de pastas."""
    root = base or CONTENT_DIR
    guides: list[Guide] = []
    if not root.is_dir():
        return guides
    for path in sorted(root.glob("*/*.md")):
        guides.append(parse(path.read_text(encoding="utf-8"), path.parent.name, path.stem))
    return guides


_CACHE: list[Guide] | None = None


def all_guides() -> list[Guide]:
    """Guias carregados uma vez por processo (o deploy recarrega)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = load_all()
    return _CACHE


def guides_for(vertical: str) -> list[Guide]:
    """Guias de um tipo de negócio."""
    return [g for g in all_guides() if g.vertical == vertical]


def get(vertical: str, slug: str) -> Guide | None:
    """Um guia pelo endereço; None quando não existe."""
    return next((g for g in all_guides() if g.vertical == vertical and g.slug == slug), None)


def problems(g: Guide, known_verticals: set[str]) -> list[str]:
    """
    Régua de boa prática de página citável. Lista vazia = pode publicar.

    Cada regra existe porque é o que faz a IA recortar e citar a página
    (ver skill huma-citacao-ia).
    """
    out: list[str] = []
    if g.vertical not in known_verticals:
        out.append(f"tipo '{g.vertical}' não existe em aeo_content.VERTICALS")
    if not _SLUG_RE.match(g.slug):
        out.append("nome do arquivo deve ser minúsculo-com-hifen, sem acento")
    if not g.titulo or len(g.titulo) > 70:
        out.append("titulo obrigatório, até 70 caracteres")
    if not g.pergunta.endswith("?"):
        out.append("pergunta obrigatória e terminando em '?' (é a busca que a página responde)")
    if not g.descricao or len(g.descricao) > 160:
        out.append("descricao obrigatória, até 160 caracteres")
    if not _DATE_RE.match(g.atualizado):
        out.append("atualizado obrigatório no formato AAAA-MM-DD")
    if not (120 <= len(g.answer) <= 450):
        out.append("primeiro parágrafo (resposta direta) entre 120 e 450 caracteres")
    if g.answer.lower().startswith(("neste artigo", "nesse artigo", "neste guia", "você já")):
        out.append("resposta direta não pode começar enrolando: responda a pergunta na primeira frase")
    if sum(1 for k, _ in g.blocks if k == "h2") < 2:
        out.append("pelo menos 2 seções '## '")
    if len(g.faq) < 3:
        out.append("pelo menos 3 perguntas em '## Perguntas frequentes' com '### '")
    if not _LINK_RE.search(g.source):
        out.append("pelo menos 1 link interno, ex.: [preço](/precos)")
    if chr(0x2014) in g.source:
        out.append("sem travessão (regra da casa)")
    return out
