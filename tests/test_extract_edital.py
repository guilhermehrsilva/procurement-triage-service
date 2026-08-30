"""Testes da orquestração de extração, sem nenhuma chamada de rede.

A cota gratuita do Gemini é curta demais para bater todo teste contra a API
de verdade (achado do M2). Aqui um LLM falso, com respostas fixas, testa a
lógica que é nossa: como o verificador reage a citação válida, inventada, ou
com valor que não bate — sem depender da API responder.
"""

from __future__ import annotations

from src.extract.extract_edital import (
    compute_divergencia,
    extract_campo_data,
    extract_campo_habilitacao,
    extract_campo_valor,
    extract_edital,
)
from src.extract.schema import CampoBruto, HabilitacaoBruta, ItemHabilitacaoBruto, PrazoBruto


class FakeLLM:
    """Substitui GeminiFieldExtractor: devolve respostas pré-definidas, na
    ordem em que `extract` é chamado, sem tocar rede."""

    def __init__(self, respostas: list):
        self._respostas = list(respostas)
        self.chamadas = 0

    def extract(self, prompt: str, response_schema):
        resposta = self._respostas[self.chamadas]
        self.chamadas += 1
        assert isinstance(resposta, response_schema), (
            f"resposta enfileirada é {type(resposta)}, esperado {response_schema}"
        )
        return resposta


PAGINAS = {
    2: "As propostas deverão ser enviadas até o dia 14/09/2026 às 09:30, impreterivelmente.",
    3: "O valor total estimado para esta contratação é de R$ 123.456,78.",
    5: "É exigido atestado de capacidade técnica para serviços similares ao objeto.",
}
DOCUMENTO = "[PÁGINA 2]\n" + PAGINAS[2] + "\n\n[PÁGINA 3]\n" + PAGINAS[3] + "\n\n[PÁGINA 5]\n" + PAGINAS[5]


def test_extract_campo_data_aceita_citacao_valida():
    llm = FakeLLM([
        PrazoBruto(
            encontrado=True,
            valor_texto="14/09/2026 09:30",
            pagina=2,
            trecho="As propostas deverão ser enviadas até o dia 14/09/2026 às 09:30, impreterivelmente.",
        )
    ])
    campo = extract_campo_data(llm, DOCUMENTO, PAGINAS)
    assert campo.motivo_nulo is None
    assert campo.valor is not None
    assert campo.citacao.pagina == 2


def test_extract_campo_data_rejeita_citacao_inventada():
    llm = FakeLLM([
        PrazoBruto(
            encontrado=True,
            valor_texto="20/10/2026",
            pagina=2,
            trecho="trecho que o LLM inventou e não existe na página 2",
        )
    ])
    campo = extract_campo_data(llm, DOCUMENTO, PAGINAS)
    assert campo.valor is None
    assert "não confere" in campo.motivo_nulo


def test_extract_campo_data_rejeita_quando_llm_nao_encontra():
    llm = FakeLLM([PrazoBruto(encontrado=False, motivo_nao_encontrado="edital não menciona prazo")])
    campo = extract_campo_data(llm, DOCUMENTO, PAGINAS)
    assert campo.valor is None
    assert campo.motivo_nulo == "edital não menciona prazo"


def test_extract_campo_data_usa_a_resposta_final_mesmo_com_candidatas_erradas():
    # achado do M3: o modelo confundia "abertura da sessão" com o prazo real.
    # datas_candidatas existe para o modelo listar as duas antes de decidir;
    # o código só usa a resposta final (encontrado/valor_texto/trecho) — o
    # teste confirma que ter uma candidata "errada" na lista não quebra nada.
    from src.extract.schema import DataCandidata

    llm = FakeLLM([
        PrazoBruto(
            datas_candidatas=[
                DataCandidata(trecho="Data de Abertura: 06/03/2026 às 09:00h", pagina=1, rotulo="abertura da sessão pública"),
                DataCandidata(trecho=PAGINAS[2], pagina=2, rotulo="prazo de entrega da proposta"),
            ],
            encontrado=True,
            valor_texto="14/09/2026 09:30",
            pagina=2,
            trecho=PAGINAS[2],
        )
    ])
    campo = extract_campo_data(llm, DOCUMENTO, PAGINAS)
    assert campo.valor is not None
    assert campo.citacao.pagina == 2


def test_extract_campo_valor_aceita_citacao_valida():
    llm = FakeLLM([
        CampoBruto(
            encontrado=True,
            valor_texto="R$ 123.456,78",
            pagina=3,
            trecho="O valor total estimado para esta contratação é de R$ 123.456,78.",
        )
    ])
    campo = extract_campo_valor(llm, DOCUMENTO, PAGINAS)
    assert campo.valor == 123456.78
    assert campo.citacao.pagina == 3


def test_extract_campo_valor_rejeita_quando_valor_nao_bate_com_trecho():
    llm = FakeLLM([
        CampoBruto(
            encontrado=True,
            valor_texto="R$ 999,00",
            pagina=3,
            trecho="O valor total estimado para esta contratação é de R$ 123.456,78.",
        )
    ])
    campo = extract_campo_valor(llm, DOCUMENTO, PAGINAS)
    assert campo.valor is None
    assert "não corresponde" in campo.motivo_nulo


def test_extract_campo_habilitacao_filtra_itens_com_citacao_invalida():
    llm = FakeLLM([
        HabilitacaoBruta(
            itens=[
                ItemHabilitacaoBruto(
                    descricao="atestado de capacidade técnica",
                    pagina=5,
                    trecho="É exigido atestado de capacidade técnica para serviços similares ao objeto.",
                ),
                ItemHabilitacaoBruto(
                    descricao="exigência inventada",
                    pagina=5,
                    trecho="isso não está na página 5",
                ),
            ]
        )
    ])
    campo = extract_campo_habilitacao(llm, DOCUMENTO, PAGINAS)
    assert len(campo.itens) == 1
    assert campo.itens[0].descricao == "atestado de capacidade técnica"
    assert campo.itens_rejeitados == 1


def test_extract_campo_habilitacao_filtra_declaracao_juridica_generica():
    # achado do M3: o LLM super-incluía declaração jurídica genérica (aqui,
    # cota para pessoa com deficiência) como se fosse habilitação técnica.
    # A citação existe de verdade no documento (não é o caso de invenção),
    # mas o filtro por palavra-chave deve rejeitar mesmo assim.
    paginas = dict(PAGINAS)
    paginas[7] = "O licitante deve declarar que cumpre as exigências de reserva de cargos para pessoa com deficiência."
    documento = DOCUMENTO + "\n\n[PÁGINA 7]\n" + paginas[7]

    llm = FakeLLM([
        HabilitacaoBruta(
            itens=[
                ItemHabilitacaoBruto(
                    descricao="atestado de capacidade técnica",
                    pagina=5,
                    trecho=PAGINAS[5],
                ),
                ItemHabilitacaoBruto(
                    descricao="declaração de cota para pessoa com deficiência",
                    pagina=7,
                    trecho=paginas[7],
                ),
            ]
        )
    ])
    campo = extract_campo_habilitacao(llm, documento, paginas)
    assert len(campo.itens) == 1
    assert campo.itens[0].descricao == "atestado de capacidade técnica"
    assert campo.itens_rejeitados == 1


def test_compute_divergencia_ambos_presentes():
    div = compute_divergencia(valor_api=100_000.0, valor_pdf=123_456.78)
    assert div.diferenca_absoluta == 23456.78
    assert div.diferenca_percentual is not None


def test_compute_divergencia_sem_nenhum_valor_retorna_none():
    assert compute_divergencia(None, None) is None


def test_extract_edital_orquestra_os_tres_campos():
    llm = FakeLLM([
        PrazoBruto(
            encontrado=True, valor_texto="14/09/2026 09:30", pagina=2,
            trecho=PAGINAS[2],
        ),
        CampoBruto(
            encontrado=True, valor_texto="R$ 123.456,78", pagina=3,
            trecho=PAGINAS[3],
        ),
        HabilitacaoBruta(itens=[
            ItemHabilitacaoBruto(descricao="atestado técnico", pagina=5, trecho=PAGINAS[5]),
        ]),
    ])
    pages = [{"page": n, "text": t} for n, t in PAGINAS.items()]
    resultado = extract_edital(llm, key="abc", numero_controle_pncp="1-2/2026", pages=pages, valor_api=0.0)

    assert resultado.prazo_entrega_proposta.valor is not None
    assert resultado.valor_estimado.valor == 123456.78
    assert len(resultado.exigencias_habilitacao.itens) == 1
    assert resultado.divergencia_valor.valor_api == 0.0
    assert resultado.divergencia_valor.valor_pdf == 123456.78
    assert llm.chamadas == 3
