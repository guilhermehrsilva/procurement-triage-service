"""Schema dos campos de decisão (M2) e do resultado bruto do LLM.

A regra que sustenta o M2: campo sem citação verificável não é extraído.
Por isso há dois níveis de modelo aqui:

- `*Bruto`: o que o LLM devolve, antes de qualquer verificação. Confiamos
  nele apenas o suficiente para saber ONDE olhar (página) e o QUE ele viu
  (trecho literal). O valor interpretado (`valor_texto`) é só um palpite.
- `Campo*`: o resultado final, depois do verificador programático
  (`verifier.py`) confirmar que o trecho existe de verdade no documento e
  que o valor extraído é consistente com ele. Se a verificação falha, o
  campo final é `None` com `motivo_nulo` preenchido — nunca um valor
  alucinado com aparência de certeza.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Bruto: saída direta do LLM, um campo por vez.
# --------------------------------------------------------------------------


class CampoBruto(BaseModel):
    """Resposta do LLM para um campo escalar (prazo, valor)."""

    encontrado: bool
    valor_texto: Optional[str] = Field(
        default=None,
        description="O valor tal como aparece no documento, em texto (ex.: '14/09/2026 09:30', 'R$ 123.456,78').",
    )
    pagina: Optional[int] = Field(default=None, description="Número da página (1-indexado) onde o valor aparece.")
    trecho: Optional[str] = Field(
        default=None,
        description="Cópia LITERAL (verbatim) do trecho do documento que contém o valor. Não parafraseie.",
    )
    motivo_nao_encontrado: Optional[str] = None


class ItemHabilitacaoBruto(BaseModel):
    descricao: str = Field(description="Resumo curto da exigência de habilitação/capacidade técnica.")
    pagina: int
    trecho: str = Field(description="Cópia LITERAL do trecho do documento que contém essa exigência.")


class HabilitacaoBruta(BaseModel):
    itens: list[ItemHabilitacaoBruto] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Final: depois da verificação programática.
# --------------------------------------------------------------------------


class Citacao(BaseModel):
    pagina: int
    trecho: str


class CampoData(BaseModel):
    valor: Optional[dt.datetime] = None
    citacao: Optional[Citacao] = None
    motivo_nulo: Optional[str] = None


class CampoValor(BaseModel):
    valor: Optional[float] = None
    citacao: Optional[Citacao] = None
    motivo_nulo: Optional[str] = None


class ExigenciaHabilitacao(BaseModel):
    descricao: str
    citacao: Citacao


class CampoHabilitacao(BaseModel):
    itens: list[ExigenciaHabilitacao] = Field(default_factory=list)
    itens_rejeitados: int = Field(
        default=0, description="Quantos itens o LLM propôs mas o verificador rejeitou (citação não confere)."
    )


class DivergenciaValor(BaseModel):
    valor_api: Optional[float]
    valor_pdf: Optional[float]
    diferenca_absoluta: Optional[float]
    diferenca_percentual: Optional[float]


class UsoLLM(BaseModel):
    """Custo e latência agregados das chamadas de LLM para este edital
    (seção 5 da proposta: custo por edital, latência p50/p95)."""

    n_chamadas: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_estimado_usd: float = 0.0
    latencia_total_segundos: float = 0.0


class ExtracaoEdital(BaseModel):
    key: str
    numero_controle_pncp: str
    prazo_entrega_proposta: CampoData
    valor_estimado: CampoValor
    exigencias_habilitacao: CampoHabilitacao
    divergencia_valor: Optional[DivergenciaValor] = None
    uso_llm: UsoLLM = Field(default_factory=UsoLLM)
