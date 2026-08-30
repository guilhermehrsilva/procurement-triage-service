"""Testes do worker de ingestão (M4). Sem rede: PNCPClient e process_one
são substituídos por fakes; só a lógica de job (thread, status, contagem)
é testada de verdade.
"""

import time
from pathlib import Path

import src.api.worker as worker_mod


class _FakeClient:
    def __init__(self, registros):
        self._registros = registros

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_contratacoes(self, data_final, max_registros=None):
        for r in self._registros[: max_registros or len(self._registros)]:
            yield r


def _registro(n: int) -> dict:
    return {"numeroControlePNCP": f"00000000000000-1-{n:06d}/2026"}


def _esperar_job_terminar(job_id: str, timeout: float = 5.0) -> dict:
    inicio = time.monotonic()
    while time.monotonic() - inicio < timeout:
        job = worker_mod.get_job(job_id)
        if job["status"] in ("concluido", "falhou"):
            return job
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} não terminou a tempo: {job}")


def test_start_ingestao_job_processa_todos_os_registros(tmp_path: Path, monkeypatch):
    registros = [_registro(i) for i in range(3)]
    monkeypatch.setattr(worker_mod, "PNCPClient", lambda: _FakeClient(registros))
    monkeypatch.setattr(worker_mod, "process_one", lambda client, cache, record: {"status": "texto_ok"})

    job_id = worker_mod.start_ingestao_job(str(tmp_path / "cache"), n=3, data_final="20260930")
    job = _esperar_job_terminar(job_id)

    assert job["status"] == "concluido"
    assert job["processados"] == 3
    assert job["n_solicitado"] == 3


def test_get_job_desconhecido_retorna_none():
    assert worker_mod.get_job("job-que-nao-existe") is None


def test_job_marca_falha_sem_derrubar_processo(tmp_path: Path, monkeypatch):
    def _client_que_falha():
        raise RuntimeError("PNCP fora do ar")

    monkeypatch.setattr(worker_mod, "PNCPClient", _client_que_falha)

    job_id = worker_mod.start_ingestao_job(str(tmp_path / "cache"), n=5, data_final="20260930")
    job = _esperar_job_terminar(job_id)

    assert job["status"] == "falhou"
    assert "PNCP fora do ar" in job["erro"]


def test_ingestao_pula_editais_que_ja_tem_texto_em_cache(tmp_path: Path, monkeypatch):
    from src.ingest.cache import DiskCache, sanitize_key

    cache_dir = tmp_path / "cache"
    cache = DiskCache(cache_dir)
    registros = [_registro(0), _registro(1)]
    cache.write_text_result(sanitize_key(registros[0]["numeroControlePNCP"]), {"status": "texto_ok"})

    chamadas = []

    def _process_one_espiao(client, cache, record):
        chamadas.append(record["numeroControlePNCP"])
        return {"status": "texto_ok"}

    monkeypatch.setattr(worker_mod, "PNCPClient", lambda: _FakeClient(registros))
    monkeypatch.setattr(worker_mod, "process_one", _process_one_espiao)

    job_id = worker_mod.start_ingestao_job(str(cache_dir), n=2, data_final="20260930")
    job = _esperar_job_terminar(job_id)

    assert job["status"] == "concluido"
    assert job["processados"] == 2  # os 2 contam como processados...
    assert chamadas == [registros[1]["numeroControlePNCP"]]  # ...mas só 1 chamou process_one
