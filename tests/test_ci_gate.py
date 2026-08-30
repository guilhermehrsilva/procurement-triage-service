"""Testes do portão de qualidade em CI (M5). Sem rede, sem LLM — só a
lógica de comparar métricas contra a baseline.
"""

from pathlib import Path

from scripts.ci_gate import calcular_metricas, checar
from src.ingest.cache import DiskCache


def _extracao(custo: float, hab_aceitos: int, hab_rejeitados: int) -> dict:
    return {
        "key": "x",
        "numero_controle_pncp": "x",
        "prazo_entrega_proposta": {"valor": None, "citacao": None, "motivo_nulo": None},
        "valor_estimado": {"valor": None, "citacao": None, "motivo_nulo": None},
        "exigencias_habilitacao": {
            "itens": [{"descricao": f"item{i}", "citacao": {"pagina": 1, "trecho": "x"}} for i in range(hab_aceitos)],
            "itens_rejeitados": hab_rejeitados,
        },
        "divergencia_valor": None,
        "uso_llm": {"custo_estimado_usd": custo, "latencia_total_segundos": 1.0},
    }


def test_calcular_metricas_agrega_custo_e_rejeicao(tmp_path: Path):
    cache = DiskCache(tmp_path / "cache")
    cache.write_extraction_result("a", _extracao(custo=0.01, hab_aceitos=1, hab_rejeitados=1))
    cache.write_extraction_result("b", _extracao(custo=0.02, hab_aceitos=0, hab_rejeitados=0))

    metricas = calcular_metricas(cache, tmp_path / "cache" / "report.json")

    assert metricas["n_editais"] == 2
    assert metricas["custo_medio_por_edital_usd"] == 0.015
    assert metricas["taxa_rejeicao_habilitacao"] == 0.5
    assert metricas["cobertura_texto_ok"] is None  # sem report.json


def test_checar_aprova_dentro_do_teto():
    metricas = {"custo_medio_por_edital_usd": 0.005, "taxa_rejeicao_habilitacao": 0.2, "cobertura_texto_ok": 0.9}
    baseline = {
        "custo_medio_maximo_por_edital_usd": 0.01,
        "taxa_rejeicao_habilitacao_maxima": 0.5,
        "cobertura_texto_minima": 0.5,
    }
    assert checar(metricas, baseline) == []


def test_checar_reprova_custo_acima_do_teto():
    metricas = {"custo_medio_por_edital_usd": 0.02, "taxa_rejeicao_habilitacao": None, "cobertura_texto_ok": None}
    baseline = {"custo_medio_maximo_por_edital_usd": 0.01}
    violacoes = checar(metricas, baseline)
    assert len(violacoes) == 1
    assert "custo médio" in violacoes[0]


def test_checar_reprova_rejeicao_acima_do_teto():
    metricas = {"custo_medio_por_edital_usd": None, "taxa_rejeicao_habilitacao": 0.8, "cobertura_texto_ok": None}
    baseline = {"taxa_rejeicao_habilitacao_maxima": 0.5}
    violacoes = checar(metricas, baseline)
    assert len(violacoes) == 1
    assert "rejeição" in violacoes[0]


def test_checar_reprova_cobertura_abaixo_do_piso():
    metricas = {"custo_medio_por_edital_usd": None, "taxa_rejeicao_habilitacao": None, "cobertura_texto_ok": 0.3}
    baseline = {"cobertura_texto_minima": 0.5}
    violacoes = checar(metricas, baseline)
    assert len(violacoes) == 1
    assert "cobertura" in violacoes[0]


def test_checar_ignora_metrica_ausente_no_baseline():
    metricas = {"custo_medio_por_edital_usd": 999.0, "taxa_rejeicao_habilitacao": None, "cobertura_texto_ok": None}
    assert checar(metricas, {}) == []
