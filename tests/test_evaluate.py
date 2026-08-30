"""Testes do harness (M3), inteiramente sobre cache local — sem rede.

Monta um cache e um conjunto dourado mínimos em disco (tmp_path) e roda as
duas camadas do harness (`--sem-llm` e com conjunto dourado) contra eles.
"""

import json
from pathlib import Path

from scripts.evaluate import eval_com_golden_set, eval_sem_llm
from src.ingest.cache import DiskCache


def _extracao_ok(key: str) -> dict:
    return {
        "key": key,
        "numero_controle_pncp": f"{key}-pncp",
        "prazo_entrega_proposta": {
            "valor": "2026-09-08T10:00:00",
            "citacao": {"pagina": 1, "trecho": "prazo até 08/09/2026 às 10h"},
            "motivo_nulo": None,
        },
        "valor_estimado": {
            "valor": 1000.0,
            "citacao": {"pagina": 2, "trecho": "R$ 1.000,00"},
            "motivo_nulo": None,
        },
        "exigencias_habilitacao": {"itens": [{"descricao": "x", "citacao": {"pagina": 3, "trecho": "x"}}], "itens_rejeitados": 1},
        "divergencia_valor": {"valor_api": 1000.0, "valor_pdf": 1000.0, "diferenca_absoluta": 0.0, "diferenca_percentual": 0.0},
        "uso_llm": {"n_chamadas": 3, "tokens_entrada": 300, "tokens_saida": 100, "custo_estimado_usd": 0.001, "latencia_total_segundos": 12.0},
    }


def _extracao_nula(key: str) -> dict:
    return {
        "key": key,
        "numero_controle_pncp": f"{key}-pncp",
        "prazo_entrega_proposta": {"valor": None, "citacao": None, "motivo_nulo": "não encontrado"},
        "valor_estimado": {"valor": None, "citacao": None, "motivo_nulo": "sigiloso"},
        "exigencias_habilitacao": {"itens": [], "itens_rejeitados": 0},
        "divergencia_valor": {"valor_api": 0.0, "valor_pdf": None, "diferenca_absoluta": None, "diferenca_percentual": None},
        "uso_llm": {"n_chamadas": 3, "tokens_entrada": 200, "tokens_saida": 50, "custo_estimado_usd": 0.0005, "latencia_total_segundos": 8.0},
    }


def test_eval_sem_llm_agrega_cache(tmp_path: Path):
    cache = DiskCache(tmp_path / "cache")
    cache.write_extraction_result("a", _extracao_ok("a"))
    cache.write_extraction_result("b", _extracao_nula("b"))

    report_m1 = tmp_path / "cache" / "report.json"
    report_m1.write_text(json.dumps({"n_processado": 10, "contagem_por_status": {"texto_ok": 9, "imagem_escaneada": 1}}))

    resultado = eval_sem_llm(cache, report_m1)

    assert resultado["n_extracoes_em_cache"] == 2
    assert resultado["verificabilidade_citacao"]["prazo_entrega_proposta"]["com_citacao_verificada"] == 1
    assert resultado["verificabilidade_citacao"]["prazo_entrega_proposta"]["nulo"] == 1
    assert resultado["verificabilidade_citacao"]["exigencias_habilitacao"]["itens_rejeitados"] == 1
    assert resultado["divergencia_valor"]["editais_com_valor_no_pdf_e_na_api"] == 1
    assert resultado["divergencia_valor"]["editais_divergentes"] == 0


def test_eval_sem_llm_sem_cache_nao_quebra(tmp_path: Path):
    cache = DiskCache(tmp_path / "cache")
    resultado = eval_sem_llm(cache, tmp_path / "cache" / "report.json")
    assert resultado["n_extracoes_em_cache"] == 0
    assert resultado["verificabilidade_citacao"] is None


def test_eval_com_golden_set_usa_apenas_cache_sem_chamar_llm(tmp_path: Path):
    cache = DiskCache(tmp_path / "cache")
    cache.write_extraction_result("a", _extracao_ok("a"))
    cache.write_extraction_result("b", _extracao_nula("b"))

    golden = {
        "editais": [
            {
                "key": "a",
                "numero_controle_pncp": "a-pncp",
                "prazo_entrega_proposta": "2026-09-08T10:00:00",
                "valor_estimado": 1000.0,
                "tem_exigencias_habilitacao_tecnica": True,
            },
            {
                "key": "b",
                "numero_controle_pncp": "b-pncp",
                "prazo_entrega_proposta": None,
                "valor_estimado": None,
                "tem_exigencias_habilitacao_tecnica": False,
            },
            {
                # não está no cache; sem --allow-llm-calls deve ser pulado, não travar
                "key": "c-sem-cache",
                "numero_controle_pncp": "c-pncp",
                "prazo_entrega_proposta": None,
                "valor_estimado": None,
                "tem_exigencias_habilitacao_tecnica": False,
            },
        ]
    }
    golden_path = tmp_path / "golden_set.json"
    golden_path.write_text(json.dumps(golden), encoding="utf-8")

    resultado = eval_com_golden_set(cache, golden_path, allow_llm_calls=False)

    assert resultado["n_no_conjunto_dourado"] == 3
    assert resultado["n_avaliados"] == 2
    assert resultado["n_pulados_sem_extracao_em_cache"] == 1
    assert resultado["acuracia_por_campo"]["prazo_entrega_proposta"] == "2/2"
    assert resultado["acuracia_por_campo"]["valor_estimado"] == "2/2"
    assert resultado["acuracia_por_campo"]["exigencias_habilitacao"]["extraido_corretamente"] == 1
    assert resultado["acuracia_por_campo"]["exigencias_habilitacao"]["abstencao_correta"] == 1
    assert resultado["custo_por_edital_usd"]["total"] == round(0.001 + 0.0005, 6)
