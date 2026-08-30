"""Serviço FastAPI (M4): /editais/{id}, /fila, /health, /metrics.

Lê exclusivamente do cache local (`data/cache/`) já populado pelos CLIs de
ingestão (M1) e extração (M2) — o serviço não chama o PNCP nem o Gemini no
ciclo de requisição (a listagem do PNCP leva 11-25s por página, e uma
extração leva vários segundos por campo; nenhum dos dois cabe num
timeout de request razoável). Ingestão contínua é responsabilidade do
worker assíncrono (`src/api/worker.py`), não deste processo.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from src.api import worker
from src.config import CACHE_DIR
from src.ingest.cache import DiskCache

app = FastAPI(
    title="procurement-triage-service",
    description="Triagem de editais do PNCP: extração com citação verificável e fila por valor esperado.",
)


def get_cache() -> DiskCache:
    return DiskCache(Path(CACHE_DIR))


@app.get("/health")
def health() -> dict[str, Any]:
    cache = get_cache()
    return {
        "status": "ok",
        "cache_dir": str(cache.root),
        "cache_existe": cache.root.exists(),
    }


@app.get("/editais/{key}")
def get_edital(key: str) -> dict[str, Any]:
    cache = get_cache()
    if not cache.has_metadata(key):
        raise HTTPException(status_code=404, detail=f"edital '{key}' não encontrado no cache")

    metadata = cache.read_metadata(key)
    cobertura_texto = cache.read_text_result(key)["status"] if cache.has_text(key) else None
    extracao = cache.read_extraction_result(key) if cache.has_extraction(key) else None

    return {
        "key": key,
        "numero_controle_pncp": metadata.get("numeroControlePNCP"),
        "orgao": metadata.get("orgaoEntidade", {}).get("razaoSocial"),
        "objeto": metadata.get("objetoCompra"),
        "data_encerramento_proposta_api": metadata.get("dataEncerramentoProposta"),
        "valor_total_estimado_api": metadata.get("valorTotalEstimado"),
        "cobertura_texto": cobertura_texto,
        "extracao": extracao,
    }


@app.get("/fila")
def get_fila(capacidade: int = Query(10, ge=1, le=1000)) -> dict[str, Any]:
    """Fila de leitura ordenada por VALOR ESPERADO (não por relevância) —
    o achado central da proposta (seção 5): sob capacidade finita, ordenar
    por valor esperado captura mais valor total do que ordenar por
    relevância. A comparação entre as duas ordenações fica para o M5.
    """
    cache = get_cache()
    itens = []

    for extracao in cache.all_extraction_results():
        key = extracao["key"]
        valor = extracao["valor_estimado"]["valor"]
        fonte = "pdf" if valor is not None else None

        if valor is None and cache.has_metadata(key):
            valor_api = cache.read_metadata(key).get("valorTotalEstimado")
            if valor_api:
                valor = valor_api
                fonte = "api (pdf não confirmou ou não trouxe valor)"

        if not valor:
            continue

        itens.append(
            {
                "key": key,
                "numero_controle_pncp": extracao["numero_controle_pncp"],
                "valor_esperado": valor,
                "fonte_valor_esperado": fonte,
                "prazo_entrega_proposta": extracao["prazo_entrega_proposta"]["valor"],
            }
        )

    itens.sort(key=lambda x: x["valor_esperado"], reverse=True)
    return {
        "capacidade": capacidade,
        "total_elegiveis": len(itens),
        "valor_total_elegivel": round(sum(i["valor_esperado"] for i in itens), 2),
        "fila": itens[:capacidade],
        "valor_capturado_na_fila": round(sum(i["valor_esperado"] for i in itens[:capacidade]), 2),
    }


@app.post("/ingestao", status_code=202)
def start_ingestao(
    n: int = Query(50, ge=1, le=1000, description="quantos editais buscar"),
    data_final: str = Query(..., description="AAAAMMDD, mesmo formato da API do PNCP"),
) -> dict[str, Any]:
    """Dispara ingestão em background (worker assíncrono, ver src/api/worker.py)
    e devolve um job_id na hora — não bloqueia esperando a listagem do PNCP."""
    job_id = worker.start_ingestao_job(CACHE_DIR, n, data_final)
    return {"job_id": job_id, "status": "enfileirado"}


@app.get("/ingestao/{job_id}")
def get_ingestao_status(job_id: str) -> dict[str, Any]:
    job = worker.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' não encontrado")
    return {"job_id": job_id, **job}


def _percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    if len(valores) == 1:
        return valores[0]
    return statistics.quantiles(valores, n=100)[int(p) - 1]


@app.get("/metrics")
def get_metrics() -> dict[str, Any]:
    """Custo acumulado, latência p50/p95 e taxa de abstenção (seção 5/6 da
    proposta) — agregados sobre tudo que já está no cache local."""
    cache = get_cache()
    extracoes = cache.all_extraction_results()

    m1_report_path = Path(CACHE_DIR) / "report.json"
    cobertura_texto_m1 = None
    if m1_report_path.exists():
        import json

        cobertura_texto_m1 = json.loads(m1_report_path.read_text(encoding="utf-8"))

    if not extracoes:
        return {
            "n_editais_extraidos": 0,
            "cobertura_texto_m1": cobertura_texto_m1,
            "custo_acumulado_usd": 0.0,
            "latencia_segundos": {"p50": None, "p95": None},
            "taxa_abstencao": None,
            "editais_com_orcamento_excedido": 0,
        }

    custos = [e["uso_llm"]["custo_estimado_usd"] for e in extracoes if e.get("uso_llm")]
    latencias = [e["uso_llm"]["latencia_total_segundos"] for e in extracoes if e.get("uso_llm")]

    n = len(extracoes)
    prazo_nulo = sum(1 for e in extracoes if e["prazo_entrega_proposta"]["valor"] is None)
    valor_nulo = sum(1 for e in extracoes if e["valor_estimado"]["valor"] is None)
    interrompidos = sum(1 for e in extracoes if e.get("motivo_interrupcao"))

    return {
        "n_editais_extraidos": n,
        "cobertura_texto_m1": cobertura_texto_m1,
        "custo_acumulado_usd": round(sum(custos), 6),
        "custo_medio_por_edital_usd": round(statistics.mean(custos), 6) if custos else None,
        "latencia_segundos": {
            "p50": round(_percentil(latencias, 50), 2) if latencias else None,
            "p95": round(_percentil(latencias, 95), 2) if latencias else None,
        },
        "taxa_abstencao": {
            "prazo_entrega_proposta": round(prazo_nulo / n, 3),
            "valor_estimado": round(valor_nulo / n, 3),
        },
        "editais_com_orcamento_excedido": interrompidos,
    }
