"""Testes do serviço FastAPI (M4). TestClient sobre um cache temporário em
disco — sem rede, sem chamada de LLM, sem depender do PNCP ou do Gemini.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.main as api_main
from src.ingest.cache import DiskCache


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cache"
    monkeypatch.setattr(api_main, "CACHE_DIR", str(d))
    return d


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _popular_edital(cache_dir: Path, key: str, *, valor_pdf=None, valor_api=None, prazo=None, custo=0.01, latencia=5.0):
    cache = DiskCache(cache_dir)
    cache.write_metadata(
        key,
        {
            "numeroControlePNCP": f"{key}-pncp",
            "orgaoEntidade": {"razaoSocial": "ÓRGÃO TESTE"},
            "objetoCompra": "objeto de teste",
            "dataEncerramentoProposta": "2026-09-08T10:00:00",
            "valorTotalEstimado": valor_api,
        },
    )
    cache.write_text_result(key, {"key": key, "status": "texto_ok"})
    cache.write_extraction_result(
        key,
        {
            "key": key,
            "numero_controle_pncp": f"{key}-pncp",
            "prazo_entrega_proposta": {"valor": prazo, "citacao": None, "motivo_nulo": None if prazo else "não encontrado"},
            "valor_estimado": {"valor": valor_pdf, "citacao": None, "motivo_nulo": None if valor_pdf else "não encontrado"},
            "exigencias_habilitacao": {"itens": [], "itens_rejeitados": 0},
            "divergencia_valor": None,
            "uso_llm": {"modelo": "fake", "n_chamadas": 3, "tokens_entrada": 100, "tokens_saida": 50,
                        "custo_estimado_usd": custo, "latencia_total_segundos": latencia},
            "motivo_interrupcao": None,
            "campos_nao_tentados": [],
        },
    )


def test_health(cache_dir, client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_edital_nao_encontrado_404(cache_dir, client):
    resp = client.get("/editais/nao-existe")
    assert resp.status_code == 404


def test_edital_encontrado(cache_dir, client):
    _popular_edital(cache_dir, "abc", valor_pdf=1000.0, prazo="2026-09-08T10:00:00")
    resp = client.get("/editais/abc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["orgao"] == "ÓRGÃO TESTE"
    assert body["extracao"]["valor_estimado"]["valor"] == 1000.0


def test_fila_ordena_por_valor_esperado_descendente(cache_dir, client):
    _popular_edital(cache_dir, "baixo", valor_pdf=100.0)
    _popular_edital(cache_dir, "alto", valor_pdf=9000.0)
    _popular_edital(cache_dir, "medio", valor_pdf=500.0)

    resp = client.get("/fila?capacidade=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_elegiveis"] == 3
    assert [i["key"] for i in body["fila"]] == ["alto", "medio"]
    assert len(body["fila"]) == 2


def test_fila_usa_valor_da_api_quando_pdf_nao_tem(cache_dir, client):
    _popular_edital(cache_dir, "sem-pdf", valor_pdf=None, valor_api=5000.0)
    resp = client.get("/fila")
    body = resp.json()
    assert body["fila"][0]["fonte_valor_esperado"].startswith("api")
    assert body["fila"][0]["valor_esperado"] == 5000.0


def test_fila_exclui_editais_sem_nenhum_valor(cache_dir, client):
    _popular_edital(cache_dir, "sem-valor-nenhum", valor_pdf=None, valor_api=None)
    resp = client.get("/fila")
    assert resp.json()["total_elegiveis"] == 0


def test_metrics_vazio_nao_quebra(cache_dir, client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.json()["n_editais_extraidos"] == 0


def test_post_ingestao_dispara_job_e_devolve_202(cache_dir, client, monkeypatch):
    monkeypatch.setattr(api_main.worker, "start_ingestao_job", lambda cache_dir, n, data_final: "job-123")
    resp = client.post("/ingestao?n=10&data_final=20260930")
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-123", "status": "enfileirado"}


def test_get_ingestao_status_desconhecido_404(cache_dir, client):
    resp = client.get("/ingestao/job-que-nao-existe")
    assert resp.status_code == 404


def test_get_ingestao_status_existente(cache_dir, client, monkeypatch):
    monkeypatch.setattr(
        api_main.worker, "get_job", lambda job_id: {"status": "concluido", "processados": 10, "n_solicitado": 10}
    )
    resp = client.get("/ingestao/job-123")
    assert resp.status_code == 200
    assert resp.json()["status"] == "concluido"


def test_metrics_agrega_custo_latencia_e_abstencao(cache_dir, client):
    _popular_edital(cache_dir, "a", valor_pdf=1000.0, prazo="2026-09-08T10:00:00", custo=0.01, latencia=5.0)
    _popular_edital(cache_dir, "b", valor_pdf=None, prazo=None, custo=0.02, latencia=10.0)

    resp = client.get("/metrics")
    body = resp.json()
    assert body["n_editais_extraidos"] == 2
    assert body["custo_acumulado_usd"] == pytest.approx(0.03)
    assert body["taxa_abstencao"]["valor_estimado"] == 0.5
    assert body["taxa_abstencao"]["prazo_entrega_proposta"] == 0.5
