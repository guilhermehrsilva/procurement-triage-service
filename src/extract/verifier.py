"""Verificação programática de citação. Sem LLM, de propósito.

Duas perguntas, nessa ordem, para cada campo extraído:

1. O trecho citado existe mesmo na página indicada? (string match, depois de
   normalizar espaço em branco). Isso pega citação inventada.
2. O valor que o LLM disse ter lido realmente aparece nesse trecho, quando
   comparado por regex de data ou moeda (não por igualdade de string)? Isso
   pega valor mal-lido ou combinado com o trecho errado.

Falhou qualquer uma das duas, o campo vira `None` com motivo. Nunca um valor
com aparência de certeza sem as duas checagens passarem.
"""

from __future__ import annotations

import datetime as dt
import re

_MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

_RE_DATA_NUMERICA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_RE_DATA_EXTENSO = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\s+de\s+(\d{4})\b", re.IGNORECASE
)
_RE_HORA = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")
_RE_MOEDA = re.compile(r"R\$\s*([\d\.]*\d(?:,\d{2})?)")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def citation_exists(trecho: str | None, page_text: str | None) -> bool:
    """O trecho citado aparece de verdade no texto da página indicada?"""
    if not trecho or not page_text:
        return False
    return normalize_ws(trecho).lower() in normalize_ws(page_text).lower()


def parse_dates(text: str) -> list[dt.datetime]:
    """Toda data (e hora, se presente) reconhecível no texto."""
    resultados: list[dt.datetime] = []

    for m in _RE_DATA_NUMERICA.finditer(text):
        dia, mes, ano = (int(x) for x in m.groups())
        if ano < 100:
            ano += 2000
        try:
            data = dt.date(ano, mes, dia)
        except ValueError:
            continue
        resultados.append(_com_hora_proxima(data, text, m.end()))

    for m in _RE_DATA_EXTENSO.finditer(text):
        dia = int(m.group(1))
        mes = _MESES[m.group(2).lower()]
        ano = int(m.group(3))
        try:
            data = dt.date(ano, mes, dia)
        except ValueError:
            continue
        resultados.append(_com_hora_proxima(data, text, m.end()))

    return resultados


def _com_hora_proxima(data: dt.date, text: str, pos: int, janela: int = 20) -> dt.datetime:
    """Se houver um horário a poucos caracteres da data, anexa. Senão, meia-noite."""
    trecho_seguinte = text[pos : pos + janela]
    m = _RE_HORA.search(trecho_seguinte)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return dt.datetime.combine(data, dt.time(h, mi))
    return dt.datetime.combine(data, dt.time.min)


def parse_currency(text: str) -> list[float]:
    """Todo valor em R$ reconhecível no texto, convertido para float."""
    resultados = []
    for m in _RE_MOEDA.finditer(text):
        bruto = m.group(1)
        try:
            resultados.append(_moeda_br_para_float(bruto))
        except ValueError:
            continue
    return resultados


def _moeda_br_para_float(valor: str) -> float:
    """'123.456,78' -> 123456.78 ; '0' -> 0.0 ; '78,00' -> 78.0"""
    if "," in valor:
        inteiro, centavos = valor.rsplit(",", 1)
        inteiro = inteiro.replace(".", "")
        return float(f"{inteiro}.{centavos}")
    return float(valor.replace(".", ""))


def verify_date_field(valor_texto: str | None, trecho: str | None) -> tuple[dt.datetime | None, str | None]:
    """Retorna (data verificada, None) ou (None, motivo)."""
    if not valor_texto:
        return None, "LLM não retornou valor_texto"
    if not trecho:
        return None, "LLM não retornou trecho de citação"

    candidatas_no_valor = parse_dates(valor_texto)
    if not candidatas_no_valor:
        return None, f"valor_texto '{valor_texto}' não contém data reconhecível"
    candidata = candidatas_no_valor[0]

    datas_no_trecho = parse_dates(trecho)
    if candidata not in datas_no_trecho:
        return None, "data extraída não corresponde a nenhuma data encontrada no trecho citado"

    return candidata, None


def verify_currency_field(valor_texto: str | None, trecho: str | None) -> tuple[float | None, str | None]:
    """Retorna (valor verificado, None) ou (None, motivo)."""
    if not valor_texto:
        return None, "LLM não retornou valor_texto"
    if not trecho:
        return None, "LLM não retornou trecho de citação"

    candidatas_no_valor = parse_currency(valor_texto)
    if not candidatas_no_valor:
        return None, f"valor_texto '{valor_texto}' não contém valor monetário reconhecível"
    candidata = candidatas_no_valor[0]

    valores_no_trecho = parse_currency(trecho)
    # tolerância de 1 centavo para arredondamento
    if not any(abs(candidata - v) < 0.01 for v in valores_no_trecho):
        return None, "valor monetário extraído não corresponde a nenhum valor encontrado no trecho citado"

    return candidata, None
