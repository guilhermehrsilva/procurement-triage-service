"""M2: extração com citação verificável.

Roda a extração LLM + verificação programática sobre os editais já
ingeridos no M1 (que renderam texto). Gera um resultado por edital em
data/cache/extractions/{key}.json e um relatório agregado.

Uso:
    python -m src.extract.run_extract --n 30

Critério de pronto (proposta, M2): rodando sobre 30 editais, todo campo
devolvido tem citação que passa na verificação, e os que não passam viram
`null` com motivo registrado.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.extract.extract_edital import extract_edital
from src.extract.gemini_client import GeminiFieldExtractor
from src.ingest.cache import DiskCache

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("extract")

STATUS_COM_TEXTO_USAVEL = {"texto_ok", "parcialmente_escaneado"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=30, help="número de editais a processar")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--report", default="data/cache/extraction_report.json")
    parser.add_argument(
        "--force", action="store_true", help="reprocessa mesmo editais que já têm extração salva"
    )
    args = parser.parse_args()

    cache = DiskCache(Path(args.cache_dir))
    llm = GeminiFieldExtractor()

    candidatos = [
        r for r in cache.all_text_results() if r.get("status") in STATUS_COM_TEXTO_USAVEL
    ]
    logger.info("Editais com texto usável no cache: %d", len(candidatos))

    contagem_prazo: Counter[str] = Counter()
    contagem_valor: Counter[str] = Counter()
    total_habilitacao_itens = 0
    total_habilitacao_rejeitados = 0
    divergencias = []
    processados = 0

    for text_result in candidatos:
        if processados >= args.n:
            break
        key = text_result["key"]
        if not args.force and cache.has_extraction(key):
            resultado = cache.read_extraction_result(key)
        else:
            metadata = cache.read_metadata(key)
            valor_api = metadata.get("valorTotalEstimado")
            try:
                extracao = extract_edital(
                    llm,
                    key=key,
                    numero_controle_pncp=text_result["numeroControlePNCP"],
                    pages=text_result["pages"],
                    valor_api=valor_api,
                )
            except Exception as exc:  # noqa: BLE001 - segue para o próximo edital
                logger.error("Falha ao extrair %s: %s", key, exc)
                continue
            resultado = json.loads(extracao.model_dump_json())
            cache.write_extraction_result(key, resultado)

        processados += 1

        prazo_ok = resultado["prazo_entrega_proposta"]["valor"] is not None
        valor_ok = resultado["valor_estimado"]["valor"] is not None
        contagem_prazo["extraido" if prazo_ok else "nulo"] += 1
        contagem_valor["extraido" if valor_ok else "nulo"] += 1
        total_habilitacao_itens += len(resultado["exigencias_habilitacao"]["itens"])
        total_habilitacao_rejeitados += resultado["exigencias_habilitacao"]["itens_rejeitados"]
        if resultado.get("divergencia_valor"):
            divergencias.append(resultado["divergencia_valor"])

        logger.info(
            "[%d/%d] %s -> prazo=%s valor=%s habilitação=%d item(ns)",
            processados,
            args.n,
            key,
            "ok" if prazo_ok else "null",
            "ok" if valor_ok else "null",
            len(resultado["exigencias_habilitacao"]["itens"]),
        )

    ambos_presentes = [
        d for d in divergencias if d["valor_api"] is not None and d["valor_pdf"] is not None
    ]
    divergentes = [d for d in ambos_presentes if d["diferenca_absoluta"] not in (None, 0)]

    report = {
        "n_solicitado": args.n,
        "n_processado": processados,
        "prazo_entrega_proposta": dict(contagem_prazo),
        "valor_estimado": dict(contagem_valor),
        "exigencias_habilitacao": {
            "itens_aceitos": total_habilitacao_itens,
            "itens_rejeitados_por_citacao_invalida": total_habilitacao_rejeitados,
        },
        "divergencia_valor_api_vs_pdf": {
            "editais_com_valor_no_pdf_e_na_api": len(ambos_presentes),
            "editais_divergentes": len(divergentes),
        },
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Relatório salvo em %s", args.report)
    logger.info("Resumo: %s", report)


if __name__ == "__main__":
    main()
