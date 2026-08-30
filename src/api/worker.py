"""Worker assíncrono de ingestão (M4).

A listagem do PNCP leva 11-25s por página — não pode rodar no ciclo de uma
requisição HTTP. `POST /ingestao` dispara o trabalho numa thread separada e
devolve um `job_id` na hora; `GET /ingestao/{job_id}` consulta o progresso.

Simplificação consciente: o estado dos jobs vive em memória, não em disco
nem numa fila persistente (Celery/RQ). Reiniciar o processo perde o
histórico de jobs — aceitável na escala deste projeto, não seria numa
operação real com múltiplas réplicas do serviço.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from src.ingest.cache import DiskCache, sanitize_key
from src.ingest.pncp_client import PNCPClient
from src.ingest.run_ingest import process_one

logger = logging.getLogger(__name__)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _executar_ingestao(job_id: str, cache_dir: str, n: int, data_final: str) -> None:
    cache = DiskCache(Path(cache_dir))
    with _jobs_lock:
        _jobs[job_id]["status"] = "rodando"

    processados = 0
    try:
        with PNCPClient() as client:
            for record in client.iter_contratacoes(data_final=data_final, max_registros=n):
                key = sanitize_key(record["numeroControlePNCP"])
                if not cache.has_text(key):
                    resultado = process_one(client, cache, record)
                    cache.write_text_result(key, resultado)
                processados += 1
                with _jobs_lock:
                    _jobs[job_id]["processados"] = processados
        with _jobs_lock:
            _jobs[job_id]["status"] = "concluido"
    except Exception as exc:  # noqa: BLE001 — job reporta o erro, não derruba o serviço
        logger.exception("Job de ingestão %s falhou", job_id)
        with _jobs_lock:
            _jobs[job_id]["status"] = "falhou"
            _jobs[job_id]["erro"] = str(exc)


def start_ingestao_job(cache_dir: str, n: int, data_final: str) -> str:
    """Inicia um job de ingestão em background e devolve o job_id na hora."""
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "enfileirado",
            "n_solicitado": n,
            "data_final": data_final,
            "processados": 0,
        }
    thread = threading.Thread(
        target=_executar_ingestao, args=(job_id, cache_dir, n, data_final), daemon=True
    )
    thread.start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None
