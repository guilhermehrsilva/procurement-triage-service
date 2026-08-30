"""Orquestra a extração de um edital: LLM por campo + verificação programática.

Fluxo (seção 4 da proposta): LLM extrai com citação -> verificador confirma
a citação sem LLM -> contrato de schema valida tipo -> divergência com a API
do PNCP é registrada, nunca escondida.
"""

from __future__ import annotations

import logging

from src.extract.gemini_client import GeminiFieldExtractor
from src.extract.prompts import (
    EXIGENCIAS_HABILITACAO,
    PRAZO_ENTREGA_PROPOSTA,
    VALOR_ESTIMADO,
    build_document_context,
)
from src.extract.schema import (
    CampoBruto,
    CampoData,
    CampoHabilitacao,
    CampoValor,
    Citacao,
    DivergenciaValor,
    ExigenciaHabilitacao,
    ExtracaoEdital,
    HabilitacaoBruta,
    PrazoBruto,
    UsoLLM,
)
from src.extract.verifier import citation_exists, verify_currency_field, verify_date_field

logger = logging.getLogger(__name__)

# Defesa em profundidade contra o achado do M3: o LLM super-inclui
# declaração jurídica/trabalhista genérica como se fosse habilitação
# técnica, mesmo com o prompt pedindo para excluir. Isto aqui não depende
# do modelo obedecer — é regex sobre o próprio trecho citado (a citação já
# passou por citation_exists, então é texto real do documento). Mesmo
# espírito da seção 8 da proposta: não usar LLM para o que regex resolve.
_FRASES_HABILITACAO_GENERICA = [
    "pessoa com deficiência",
    "reabilitado da previdência",
    "menor de 18",
    "menor de 16",
    "trabalho degradante",
    "trabalho forçado",
    "empresas punidas",  # Cadastro Nacional de Empresas Punidas (CNEP)
    "cnep",
    "sociedade cooperativa",
    "art. 16 da lei",  # regras de cooperativa, Lei 14.133/2021
    "atende aos requisitos de habilitação",
    "condições do edital",
    "custos trabalhistas",
]


def _e_habilitacao_generica(descricao: str, trecho: str) -> bool:
    texto = f"{descricao} {trecho}".lower()
    return any(frase in texto for frase in _FRASES_HABILITACAO_GENERICA)


def _paginas_por_numero(pages: list[dict]) -> dict[int, str]:
    return {p["page"]: p["text"] for p in pages}


def extract_campo_data(
    llm: GeminiFieldExtractor, documento: str, paginas: dict[int, str]
) -> CampoData:
    bruto: PrazoBruto = llm.extract(PRAZO_ENTREGA_PROPOSTA.format(documento=documento), PrazoBruto)

    if not bruto.encontrado:
        return CampoData(motivo_nulo=bruto.motivo_nao_encontrado or "LLM não encontrou o campo")

    texto_pagina = paginas.get(bruto.pagina or -1)
    if not citation_exists(bruto.trecho, texto_pagina):
        return CampoData(motivo_nulo="citação não confere com o texto da página indicada")

    valor, motivo = verify_date_field(bruto.valor_texto, bruto.trecho)
    if motivo:
        return CampoData(motivo_nulo=motivo)

    return CampoData(valor=valor, citacao=Citacao(pagina=bruto.pagina, trecho=bruto.trecho))


def extract_campo_valor(
    llm: GeminiFieldExtractor, documento: str, paginas: dict[int, str]
) -> CampoValor:
    bruto: CampoBruto = llm.extract(VALOR_ESTIMADO.format(documento=documento), CampoBruto)

    if not bruto.encontrado:
        return CampoValor(motivo_nulo=bruto.motivo_nao_encontrado or "LLM não encontrou o campo")

    texto_pagina = paginas.get(bruto.pagina or -1)
    if not citation_exists(bruto.trecho, texto_pagina):
        return CampoValor(motivo_nulo="citação não confere com o texto da página indicada")

    valor, motivo = verify_currency_field(bruto.valor_texto, bruto.trecho)
    if motivo:
        return CampoValor(motivo_nulo=motivo)

    return CampoValor(valor=valor, citacao=Citacao(pagina=bruto.pagina, trecho=bruto.trecho))


def extract_campo_habilitacao(
    llm: GeminiFieldExtractor, documento: str, paginas: dict[int, str]
) -> CampoHabilitacao:
    bruto: HabilitacaoBruta = llm.extract(EXIGENCIAS_HABILITACAO.format(documento=documento), HabilitacaoBruta)

    itens_validos = []
    rejeitados = 0
    for item in bruto.itens:
        texto_pagina = paginas.get(item.pagina)
        if not citation_exists(item.trecho, texto_pagina):
            rejeitados += 1
            logger.warning("Exigência de habilitação rejeitada (citação não confere): %r", item.descricao)
            continue
        if _e_habilitacao_generica(item.descricao, item.trecho):
            rejeitados += 1
            logger.warning("Exigência de habilitação rejeitada (declaração jurídica genérica): %r", item.descricao)
            continue
        itens_validos.append(
            ExigenciaHabilitacao(descricao=item.descricao, citacao=Citacao(pagina=item.pagina, trecho=item.trecho))
        )

    return CampoHabilitacao(itens=itens_validos, itens_rejeitados=rejeitados)


def compute_divergencia(valor_api: float | None, valor_pdf: float | None) -> DivergenciaValor | None:
    if valor_api is None and valor_pdf is None:
        return None
    diff_abs = None
    diff_pct = None
    if valor_api is not None and valor_pdf is not None:
        diff_abs = round(valor_pdf - valor_api, 2)
        diff_pct = round(diff_abs / valor_api * 100, 2) if valor_api else None
    return DivergenciaValor(
        valor_api=valor_api, valor_pdf=valor_pdf, diferenca_absoluta=diff_abs, diferenca_percentual=diff_pct
    )


def extract_edital(
    llm: GeminiFieldExtractor, key: str, numero_controle_pncp: str, pages: list[dict], valor_api: float | None
) -> ExtracaoEdital:
    documento = build_document_context(pages)
    paginas = _paginas_por_numero(pages)

    if hasattr(llm, "reset_usage"):
        llm.reset_usage()

    prazo = extract_campo_data(llm, documento, paginas)
    valor = extract_campo_valor(llm, documento, paginas)
    habilitacao = extract_campo_habilitacao(llm, documento, paginas)
    divergencia = compute_divergencia(valor_api, valor.valor)

    uso_llm = UsoLLM(**llm.usage_snapshot()) if hasattr(llm, "usage_snapshot") else UsoLLM()

    return ExtracaoEdital(
        key=key,
        numero_controle_pncp=numero_controle_pncp,
        prazo_entrega_proposta=prazo,
        valor_estimado=valor,
        exigencias_habilitacao=habilitacao,
        divergencia_valor=divergencia,
        uso_llm=uso_llm,
    )
