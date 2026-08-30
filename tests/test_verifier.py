import datetime as dt

from src.extract.verifier import (
    citation_exists,
    parse_currency,
    parse_dates,
    verify_currency_field,
    verify_date_field,
)


def test_citation_exists_ignores_whitespace_and_case():
    page = "As propostas   deverão\nser enviadas até 14/09/2026."
    assert citation_exists("as propostas deverão ser enviadas até 14/09/2026.", page)


def test_citation_missing_returns_false():
    assert not citation_exists("trecho que não existe no documento", "outro conteúdo qualquer")


def test_citation_exists_false_when_empty():
    assert not citation_exists(None, "algum texto")
    assert not citation_exists("trecho", None)


def test_parse_dates_numeric_with_time():
    datas = parse_dates("enviadas até o dia 14/09/2026 às 09:30, impreterivelmente")
    assert dt.datetime(2026, 9, 14, 9, 30) in datas


def test_parse_dates_numeric_without_time_defaults_midnight():
    datas = parse_dates("prazo final: 01/12/2026.")
    assert dt.datetime(2026, 12, 1, 0, 0) in datas


def test_parse_dates_por_extenso():
    datas = parse_dates("São Paulo, 14 de setembro de 2026.")
    assert dt.datetime(2026, 9, 14, 0, 0) in datas


def test_parse_currency_brl_format():
    valores = parse_currency("o valor estimado é de R$ 123.456,78 para o item")
    assert 123456.78 in valores


def test_parse_currency_zero():
    assert parse_currency("valor: R$ 0,00 (sigiloso)") == [0.0]


def test_verify_date_field_success():
    valor, motivo = verify_date_field(
        "14/09/2026 09:30",
        "As propostas deverão ser enviadas até o dia 14/09/2026 às 09:30.",
    )
    assert valor == dt.datetime(2026, 9, 14, 9, 30)
    assert motivo is None


def test_verify_date_field_rejects_mismatched_trecho():
    # o valor_texto diz uma data que não está no trecho citado -> null
    valor, motivo = verify_date_field(
        "20/10/2026",
        "As propostas deverão ser enviadas até o dia 14/09/2026 às 09:30.",
    )
    assert valor is None
    assert "não corresponde" in motivo


def test_verify_date_field_rejects_missing_trecho():
    valor, motivo = verify_date_field("14/09/2026", None)
    assert valor is None
    assert motivo is not None


def test_verify_currency_field_success():
    valor, motivo = verify_currency_field(
        "R$ 123.456,78",
        "O valor total estimado é de R$ 123.456,78 para esta contratação.",
    )
    assert valor == 123456.78
    assert motivo is None


def test_verify_currency_field_rejects_mismatched_trecho():
    valor, motivo = verify_currency_field(
        "R$ 999,00",
        "O valor total estimado é de R$ 123.456,78 para esta contratação.",
    )
    assert valor is None
    assert "não corresponde" in motivo


def test_verify_currency_field_rejects_unparseable_valor_texto():
    valor, motivo = verify_currency_field("valor sigiloso", "texto qualquer com R$ 10,00")
    assert valor is None
    assert motivo is not None
