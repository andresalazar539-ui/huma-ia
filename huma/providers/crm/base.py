# ================================================================
# huma/providers/crm/base.py — Contrato de integração com CRM
#
# CRM NÃO é uma Capability (huma.core.capabilities). As capabilities
# descrevem o que o clone FAZ com o lead (agendar, qualificar, vender).
# CRM é infraestrutura de ESPELHAMENTO de resultado: quando um lead
# vira pipeline (qualificado/agendado), a HUMA reflete isso no CRM do
# dono pra fechar o loop "HUMA mandou o lead → virou negócio → virou
# venda".
#
# Por isso o CRM sync é disparado como efeito colateral SILENCIOSO no
# orchestrator (não como action da IA): atribuição precisa ser
# confiável (todo lead que vira pipeline é empurrado, não depende do
# Claude lembrar) e custo zero de token (não entra na tool description).
#
# Ativado pela presença de credencial (identity.crm_provider != "" +
# tokens). Sem credencial, o resolver devolve None e o orchestrator
# simplesmente não sincroniza — degrada gracioso, igual Bling/Redis.
#
# Implementações concretas vivem em subpastas por provider
# (pipedrive.py, rd_station.py) e SEMPRE retornam dict — nunca
# propagam exceção, pra orchestrator decidir como degradar sem
# nunca quebrar a conversa com o lead.
# ================================================================

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity


class CRMProvider(ABC):
    """
    Contrato pra integração com CRM (Pipedrive, RD Station, ...).

    Implementações: PipedriveAdapter (Fase B), RDStationAdapter (Fase E).

    Três métodos OUTBOUND (HUMA → CRM), chamados pelo orchestrator
    quando o lead vira pipeline:
        upsert_lead   — contato (dedup por telefone/email, origem=HUMA)
        upsert_deal   — negócio no pipeline+estágio mapeado do dono
        log_activity  — nota/reunião na timeline do negócio

    Um método INBOUND (CRM → HUMA), chamado pela rota de webhook:
        parse_outcome — normaliza notificação de ganho/perdido pra
                        fechar o loop de atribuição.

    IMPORTANTE — mapeamento de estágio: quando a HUMA cria o negócio
    no handoff/agendamento, ele entra no estágio QUALIFICADO
    (identity.crm_stage_id), NUNCA em "ganho". "Ganho" no CRM é venda
    fechada e quem decide isso é o humano — a HUMA só descobre via
    parse_outcome. Não confundir com o stage="won" do funil da HUMA,
    que significa apenas "saiu do pipeline da IA com sucesso".

    Todos os métodos async NUNCA propagam exceção. Falhas de
    rede/auth viram status="error" e o orchestrator loga + segue
    (a conversa com o lead jamais é afetada por falha de CRM).
    """

    @abstractmethod
    async def upsert_lead(
        self,
        identity: "ClientIdentity",
        lead: dict,
    ) -> dict:
        """
        Cria ou atualiza o contato no CRM, com dedup.

        Faz match por telefone/email ANTES de criar — encher o CRM do
        dono de contato duplicado é a dor nº1 de integração ruim.
        Carimba a origem como HUMA (campo/source do CRM) pra atribuição.

        Args:
            identity: ClientIdentity com credenciais + mapeamento do CRM.
            lead: dict com chaves:
                phone (str)            — telefone do lead (chave de dedup)
                name (str | "")        — nome canônico do lead
                email (str | "")       — email coletado (chave de dedup)
                facts (list[str])      — fatos qualificadores coletados

        Returns:
            {"status": "ok", "crm_contact_id": str}
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...

    @abstractmethod
    async def upsert_deal(
        self,
        identity: "ClientIdentity",
        deal: dict,
    ) -> dict:
        """
        Cria ou atualiza o negócio no pipeline+estágio mapeado.

        Usa identity.crm_pipeline_id + identity.crm_stage_id (estágio
        QUALIFICADO, nunca "ganho"). Linka ao contato (crm_contact_id)
        e, se houver, atribui ao dono default (identity.crm_owner_id).
        Carimba origem=HUMA.

        Idempotência: se já existe crm_deal_id pra essa conversa, o
        caller passa em deal["crm_deal_id"] e a implementação ATUALIZA
        em vez de criar outro — evita negócio duplicado a cada turn.

        Args:
            identity: ClientIdentity com credenciais + mapeamento.
            deal: dict com chaves:
                crm_contact_id (str)   — contato do upsert_lead
                title (str)            — título do negócio
                value_cents (int | 0)  — valor estimado, se houver
                crm_deal_id (str | "") — se preenchido, atualiza

        Returns:
            {"status": "ok", "crm_deal_id": str}
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...

    @abstractmethod
    async def log_activity(
        self,
        identity: "ClientIdentity",
        activity: dict,
    ) -> dict:
        """
        Registra nota/atividade na timeline do negócio.

        Usado pra dar ao closer contexto total: resumo da conversa no
        handoff, ou a reunião agendada (com data/serviço) no
        agendamento. O humano abre o negócio e vê tudo.

        Args:
            identity: ClientIdentity com credenciais.
            activity: dict com chaves:
                crm_deal_id (str)      — negócio alvo
                kind (str)             — "note" | "meeting"
                summary (str)          — texto da nota / título da reunião
                when (str | "")        — ISO datetime (só meeting)

        Returns:
            {"status": "ok"}
            {"status": "no_credentials"}
            {"status": "error", "detail": str}
        """
        ...

    @abstractmethod
    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Normaliza webhook inbound de mudança de negócio → atribuição.

        Síncrono de propósito (só parsing, sem I/O). Cada CRM tem shape
        de webhook diferente; a rota /webhook/crm/{provider} chama isso
        pra extrair o ID do negócio e se virou ganho/perdido, depois
        casa pelo crm_deal_id guardado na Conversation.

        Pra CRMs com webhook fraco (RD), o mesmo método é alimentado
        por um poll periódico que monta um payload equivalente.

        Args:
            payload: corpo do webhook (já parseado de JSON).
            headers: headers da request (pra assinatura/validação).

        Returns:
            {"crm_deal_id": str, "outcome": "won" | "lost" | "unknown"}
        """
        ...
