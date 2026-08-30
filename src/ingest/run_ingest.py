"""M1: ingestão e corpus.

Busca contratações com proposta em aberto (Pregão Eletrônico), baixa o
pacote de documentos de cada uma, extrai texto do PDF do edital e grava tudo
em cache local deduplicado por numeroControlePNCP.

Uso:
    python -m src.ingest.run_ingest --n 300 --data-final 20260930

Critério de pronto (proposta, M1): 300 editais em cache, com relatório de
quantos renderam texto, quantos são imagem e quantos falharam, e por quê.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from src.ingest.cache import DiskCache, sanitize_key
from src.ingest.pdf_text import (
    classify_extraction,
    extract_documents,
    extract_pages,
    pick_edital_pdf,
)
from src.ingest.pncp_client import PNCPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("ingest")


def process_one(client: PNCPClient, cache: DiskCache, record: dict) -> dict:
    """Processa um edital de ponta a ponta. Nunca lança — retorna status."""
    key = sanitize_key(record["numeroControlePNCP"])
    cache.write_metadata(key, record)

    cnpj = record["orgaoEntidade"]["cnpj"]
    ano = record["anoCompra"]
    sequencial = record["sequencialCompra"]

    try:
        arquivos = client.get_arquivos_metadata(cnpj, ano, sequencial)
    except Exception as exc:  # noqa: BLE001 - queremos continuar o lote
        return _result(key, record, status="falha_metadata_arquivos", detail=str(exc))

    editais = [a for a in arquivos if a.get("tipoDocumentoNome", "").lower() == "edital"]
    alvo = editais[0] if editais else (arquivos[0] if arquivos else None)
    if alvo is None:
        return _result(key, record, status="sem_documento", detail="nenhum arquivo listado")

    seq_doc = alvo["sequencialDocumento"]

    try:
        if not cache.has_zip(key, seq_doc):
            content = client.download_arquivo(cnpj, ano, sequencial, seq_doc)
            cache.write_zip(key, seq_doc, content)
    except Exception as exc:  # noqa: BLE001
        return _result(key, record, status="falha_download", detail=str(exc))

    try:
        pdf_paths = extract_documents(cache.zip_path(key, seq_doc), cache.extract_dir(key, seq_doc))
    except Exception as exc:  # noqa: BLE001
        return _result(key, record, status="falha_extracao_arquivo", detail=str(exc))

    edital_pdf = pick_edital_pdf(pdf_paths)
    if edital_pdf is None:
        return _result(key, record, status="sem_pdf_no_pacote", detail=f"{len(pdf_paths)} pdf(s) encontrado(s)")

    try:
        pages = extract_pages(edital_pdf)
    except Exception as exc:  # noqa: BLE001
        return _result(key, record, status="falha_extracao_texto", detail=str(exc))

    classificacao = classify_extraction(pages)
    return _result(
        key,
        record,
        status=classificacao,
        detail=None,
        num_paginas=len(pages),
        arquivo_pdf=edital_pdf.name,
        pages=[{"page": p.page_number, "text": p.text} for p in pages],
    )


def _result(
    key: str,
    record: dict,
    *,
    status: str,
    detail: str | None,
    num_paginas: int | None = None,
    arquivo_pdf: str | None = None,
    pages: list[dict] | None = None,
) -> dict:
    return {
        "key": key,
        "numeroControlePNCP": record["numeroControlePNCP"],
        "orgao": record.get("orgaoEntidade", {}).get("razaoSocial"),
        "status": status,
        "detail": detail,
        "num_paginas": num_paginas,
        "arquivo_pdf": arquivo_pdf,
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-final", default="20260930", help="AAAAMMDD")
    parser.add_argument("--n", type=int, default=300, help="número de editais a ingerir")
    parser.add_argument("--tamanho-pagina", type=int, default=50)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--report", default="data/cache/report.json")
    parser.add_argument(
        "--skip-cached", action="store_true", default=True,
        help="pula editais que já têm texto extraído no cache (padrão: liga)",
    )
    args = parser.parse_args()

    cache = DiskCache(Path(args.cache_dir))
    counts: Counter[str] = Counter()
    processados = 0
    t0 = time.monotonic()

    with PNCPClient() as client:
        for record in client.iter_contratacoes(
            data_final=args.data_final,
            tamanho_pagina=args.tamanho_pagina,
            max_registros=args.n,
        ):
            key = sanitize_key(record["numeroControlePNCP"])
            if args.skip_cached and cache.has_text(key):
                result = cache.read_text_result(key)
            else:
                result = process_one(client, cache, record)
                # não regrava o texto completo das páginas no result "leve"
                cache.write_text_result(key, result)

            counts[result["status"]] += 1
            processados += 1
            logger.info(
                "[%d/%d] %s -> %s (%s)",
                processados, args.n, result["numeroControlePNCP"], result["status"],
                result.get("orgao") or "",
            )

    elapsed = time.monotonic() - t0
    report = {
        "data_final": args.data_final,
        "n_solicitado": args.n,
        "n_processado": processados,
        "elapsed_seconds": round(elapsed, 1),
        "contagem_por_status": dict(counts),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Relatório salvo em %s", args.report)
    logger.info("Resumo: %s", dict(counts))


if __name__ == "__main__":
    main()
