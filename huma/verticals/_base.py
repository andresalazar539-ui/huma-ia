# ================================================================
# huma/verticals/_base.py — Estrutura do "cérebro" de uma vertical
#
# Um cérebro é o currículo do funcionário especialista que a HUMA
# vira quando o dono escolhe a categoria: jornada de decisão do
# comprador, perguntas de descoberta na ordem certa, perfis de lead,
# leitura do lead → como agir, objeções com técnica e molde de
# resposta, gatilhos permitidos/proibidos, sinais de compra, limites
# legais e exemplos de conversa.
#
# Regras de construção (ler antes de escrever um cérebro novo):
#   - Toda instrução de comportamento é CONDICIONAL (SE/QUANDO).
#     Regra "SEMPRE faça X" faz a IA abandonar o rapport pra empurrar X.
#   - O tom do DONO manda. O bloco de tom da vertical é a fronteira
#     (o que a vertical não tolera), não o estilo final.
#   - Gatilho mental só com fato real cadastrado. Escassez inventada
#     destrói confiança e é CDC.
#   - Exemplos são ESTILO, não script. Sem markdown, sem travessão,
#     sem "Claro!", sem pergunta forçada no fim de toda mensagem.
#   - O conhecimento da área é repertório pra ENTENDER e CONDUZIR.
#     O que a IA OFERECE é só o que está em PRODUTOS/SERVIÇOS e FAQ.
#
# O bloco renderizado entra no prompt ESTÁTICO (cacheado). Tamanho
# alvo por vertical: 7.000-10.000 chars (~3.000-4.000 tokens). Isso
# também garante que verticais antes "finas" (e-commerce, imob)
# ultrapassem o mínimo de cache do Haiku (ver min_chars_for_cache).
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Objecao:
    """Uma objeção típica da vertical com a técnica e um molde de resposta."""
    gatilho: str      # o que o lead diz (sinais)
    tecnica: str      # como tratar (1-2 frases)
    molde: str        # exemplo de resposta com placeholders {produto}, {diferencial}, {prova}


@dataclass(frozen=True)
class Perfil:
    """Perfil de lead típico da vertical, com sinais de detecção."""
    nome: str
    sinais: str       # palavras/comportamentos que denunciam o perfil
    como_tratar: str  # tom + ordem de conversa


@dataclass(frozen=True)
class Exemplo:
    """Mini-diálogo exemplar (estilo, não script)."""
    lead: str
    resposta: str
    por_que: str


@dataclass(frozen=True)
class VerticalBrain:
    """Cérebro completo de uma vertical. Renderizado por `render()`."""
    slug: str
    titulo_tom: str                 # ex.: "TOM CLÍNICA — CONSULTORA DE SAÚDE" (tests dependem do prefixo TOM)
    tom: str                        # fronteiras de tom da vertical (o tom do dono manda dentro delas)
    jornada: str                    # jornada de decisão do comprador dessa área
    descoberta: list[str]           # perguntas que o especialista faz, em ordem
    perfis: list[Perfil]
    leitura: list[str]              # "SE lead X → faça Y"
    objecoes: list[Objecao]
    gatilhos_ok: list[str]
    gatilhos_proibidos: list[str]
    sinais_de_compra: list[str]
    preco: str                      # como tratar preço nesta vertical
    meta_padrao: str                # a meta que o funcionário persegue por baixo dos panos
    erros_fatais: list[str]
    limites: list[str]              # lei/ética/regulação
    valorizam: str
    odeiam: str
    followup: str                   # vira a linha "FOLLOW-UP:" (usada pelo generate_followup_message)
    exemplos: list[Exemplo] = field(default_factory=list)

    def render(self) -> str:
        """Renderiza o cérebro como bloco de prompt (texto puro, PT-BR)."""
        linhas: list[str] = []
        add = linhas.append

        add("")
        add(f"{self.titulo_tom}:")
        add(f"  {self.tom.strip()}")
        add("  SE o dono definiu um tom em IDENTIDADE, o tom dele manda dentro destas fronteiras.")

        add("")
        add(f"CÉREBRO DA VERTICAL ({self.slug}) — você é especialista nesta área.")
        add("  Use este repertório A FAVOR do negócio acima: ofereça SÓ o que está em PRODUTOS/SERVIÇOS e FAQ.")
        add("  O resto é conhecimento pra entender o lead, orientar e conduzir. NUNCA vira oferta, preço ou promessa inventada.")

        add("")
        add(f"  JORNADA DE DECISÃO: {self.jornada.strip()}")

        add("")
        add("  DESCOBERTA (o que o especialista descobre, UMA pergunta por vez, SÓ o que ainda não sabe):")
        for i, q in enumerate(self.descoberta, 1):
            add(f"    {i}. {q}")

        add("")
        add("  PERFIS (detecte pelos sinais, adapte tom e ordem):")
        for p in self.perfis:
            add(f"    {p.nome}: sinais {p.sinais}. {p.como_tratar}")

        add("")
        add("  LEITURA DO LEAD → COMO AGIR:")
        for l in self.leitura:
            add(f"    {l}")

        add("")
        add("  OBJEÇÕES → TÉCNICA → EXEMPLO (adapte aos produtos, nomes e fatos reais do negócio; nunca copie literal):")
        for o in self.objecoes:
            add(f"    \"{o.gatilho}\" → {o.tecnica}")
            add(f"      Ex.: {o.molde}")

        add("")
        add("  GATILHOS QUE FUNCIONAM AQUI (só com fato real cadastrado ou verificado pelo sistema):")
        for g in self.gatilhos_ok:
            add(f"    - {g}")
        add("  GATILHOS PROIBIDOS:")
        for g in self.gatilhos_proibidos:
            add(f"    - {g}")

        add("")
        add("  SINAIS DE COMPRA (quando aparecer, avance com naturalidade):")
        add("    " + "; ".join(self.sinais_de_compra))

        add("")
        add(f"  PREÇO NESTA VERTICAL: {self.preco.strip()}")

        add("")
        add(f"  META (por baixo dos panos): {self.meta_padrao.strip()}")

        add("")
        add("  ERROS FATAIS: " + "; ".join(self.erros_fatais) + ".")
        add("  LIMITES (lei/ética): " + "; ".join(self.limites) + ".")
        add(f"  O QUE VALORIZAM: {self.valorizam.strip()}")
        add(f"  O QUE ODEIAM: {self.odeiam.strip()}")

        add("")
        add(f"  FOLLOW-UP: {self.followup.strip()}")

        if self.exemplos:
            add("")
            add("  EXEMPLOS DE CONVERSA (estilo, não script; troque nomes, produtos e fatos pelos reais):")
            for e in self.exemplos:
                add(f"    Lead: \"{e.lead}\"")
                add(f"    Você: \"{e.resposta}\"")
                add(f"      (por quê: {e.por_que})")

        return "\n".join(linhas)
