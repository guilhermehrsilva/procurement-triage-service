"""M3: harness de avaliação.

Duas camadas, como a proposta pede (seção 5):

1. Sem LLM (`--sem-llm`), sempre disponível, roda de graça: cobertura de
   texto (M1), verificabilidade de citação e divergência API x PDF (M2),
   agregadas sobre o que já está em cache. Nenhuma chamada de rede.

2. Com LLM (padrão): compara o conjunto dourado (`data/golden_set.json`,
   rotulado à mão, seção 8: nunca gerado por LLM) contra extrações já em
   cache. Por padrão NÃO chama o Gemini de novo — a cota gratuita é curta
   demais para depender dela em toda rodada do harness (achado do M2). Use
   `--allow-llm-calls` para extrair o que faltar no cache.

Uso:
    python -m scripts.evaluate --sem-llm
    python -m scripts.evaluate
    python -m scripts.evaluate --allow-llm-calls
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
from pathlib import Path

from src.ingest.cache import DiskCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("evaluate")

TOLERANCIA_VALOR = 0.01
TOLERANCIA_PRAZO_MINUTOS = 1


def eval_sem_llm(cache: DiskCache, m1_report_path: Path) -> dict:
    """Métricas que não exigem nenhuma chamada de LLM."""
    resultado: dict = {}

    if m1_report_path.exists():
        resultado["cobertura_texto_m1"] = json.loads(m1_report_path.read_text(encoding="utf-8"))
    else:
        resultado["cobertura_texto_m1"] = None
        logger.warning("Relatório do M1 não encontrado em %s", m1_report_path)

    extracoes = cache.all_extraction_results()
    resultado["n_extracoes_em_cache"] = len(extracoes)

    if not extracoes:
        resultado["verificabilidade_citacao"] = None
        resultado["divergencia_valor"] = None
        return resultado

    prazo_com_citacao = sum(1 for e in extracoes if e["prazo_entrega_proposta"]["citacao"] is not None)
    valor_com_citacao = sum(1 for e in extracoes if e["valor_estimado"]["citacao"] is not None)
    hab_itens_aceitos = sum(len(e["exigencias_habilitacao"]["itens"]) for e in extracoes)
    hab_itens_rejeitados = sum(e["exigencias_habilitacao"]["itens_rejeitados"] for e in extracoes)
    hab_total_propostos = hab_itens_aceitos + hab_itens_rejeitados

    resultado["verificabilidade_citacao"] = {
        "prazo_entrega_proposta": {
            "com_citacao_verificada": prazo_com_citacao,
            "nulo": len(extracoes) - prazo_com_citacao,
        },
        "valor_estimado": {
            "com_citacao_verificada": valor_com_citacao,
            "nulo": len(extracoes) - valor_com_citacao,
        },
        "exigencias_habilitacao": {
            "itens_aceitos": hab_itens_aceitos,
            "itens_rejeitados": hab_itens_rejeitados,
            "taxa_rejeicao": round(hab_itens_rejeitados / hab_total_propostos, 3) if hab_total_propostos else None,
        },
        "nota": "100% dos campos e itens aqui, por construção, têm citação que passou na "
        "verificação (verifier.py) — o que falhou já virou null/foi descartado antes de chegar aqui.",
    }

    divergencias = [e["divergencia_valor"] for e in extracoes if e.get("divergencia_valor")]
    ambos = [d for d in divergencias if d["valor_api"] is not None and d["valor_pdf"] is not None]
    divergentes = [d for d in ambos if d["diferenca_absoluta"] not in (None, 0)]
    resultado["divergencia_valor"] = {
        "editais_com_valor_no_pdf_e_na_api": len(ambos),
        "editais_divergentes": len(divergentes),
        "diferenca_percentual_media_dos_divergentes": (
            round(statistics.mean(abs(d["diferenca_percentual"]) for d in divergentes if d["diferenca_percentual"] is not None), 2)
            if divergentes
            else None
        ),
    }

    return resultado


def _parse_prazo_golden(valor: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(valor) if valor else None


def eval_com_golden_set(cache: DiskCache, golden_path: Path, allow_llm_calls: bool) -> dict:
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    editais = golden["editais"]

    llm = None
    if allow_llm_calls:
        from dotenv import load_dotenv

        load_dotenv()
        from src.extract.extract_edital import extract_edital
        from src.extract.gemini_client import GeminiFieldExtractor

        llm = GeminiFieldExtractor()

    acertos_prazo = erros_prazo = 0
    acertos_valor = erros_valor = 0
    habilitacao_classificacao = {
        "extraido_corretamente": 0,
        "abstencao_correta": 0,
        "abstencao_covarde": 0,
        "extraido_indevidamente": 0,  # golden diz que não há, sistema encontrou itens (falso positivo)
    }
    custos_usd = []
    latencias_s = []
    detalhes = []
    pulados = 0

    for entry in editais:
        key = entry["key"]
        if not cache.has_extraction(key):
            if not allow_llm_calls:
                logger.warning("Sem extração em cache para %s e --allow-llm-calls não foi passado; pulando.", key)
                pulados += 1
                continue
            metadata = cache.read_metadata(key)
            text_result = cache.read_text_result(key)
            extracao = extract_edital(
                llm,
                key=key,
                numero_controle_pncp=entry["numero_controle_pncp"],
                pages=text_result["pages"],
                valor_api=metadata.get("valorTotalEstimado"),
            )
            resultado = json.loads(extracao.model_dump_json())
            cache.write_extraction_result(key, resultado)
        else:
            resultado = cache.read_extraction_result(key)

        detalhe = {"key": key}

        golden_prazo = _parse_prazo_golden(entry["prazo_entrega_proposta"])
        sistema_prazo_str = resultado["prazo_entrega_proposta"]["valor"]
        sistema_prazo = dt.datetime.fromisoformat(sistema_prazo_str) if sistema_prazo_str else None
        if golden_prazo is None and sistema_prazo is None:
            acertos_prazo += 1
            detalhe["prazo"] = "ok (ambos nulos)"
        elif golden_prazo is not None and sistema_prazo is not None and abs(
            (golden_prazo - sistema_prazo).total_seconds()
        ) <= TOLERANCIA_PRAZO_MINUTOS * 60:
            acertos_prazo += 1
            detalhe["prazo"] = "ok"
        else:
            erros_prazo += 1
            detalhe["prazo"] = f"erro: golden={golden_prazo} sistema={sistema_prazo}"

        golden_valor = entry["valor_estimado"]
        sistema_valor = resultado["valor_estimado"]["valor"]
        if golden_valor is None and sistema_valor is None:
            acertos_valor += 1
            detalhe["valor"] = "ok (ambos nulos)"
        elif golden_valor is not None and sistema_valor is not None and abs(golden_valor - sistema_valor) < TOLERANCIA_VALOR:
            acertos_valor += 1
            detalhe["valor"] = "ok"
        else:
            erros_valor += 1
            detalhe["valor"] = f"erro: golden={golden_valor} sistema={sistema_valor}"

        tem_hab_golden = entry["tem_exigencias_habilitacao_tecnica"]
        tem_hab_sistema = len(resultado["exigencias_habilitacao"]["itens"]) > 0
        if tem_hab_golden and tem_hab_sistema:
            habilitacao_classificacao["extraido_corretamente"] += 1
        elif not tem_hab_golden and not tem_hab_sistema:
            habilitacao_classificacao["abstencao_correta"] += 1
        elif tem_hab_golden and not tem_hab_sistema:
            habilitacao_classificacao["abstencao_covarde"] += 1
        else:  # not tem_hab_golden and tem_hab_sistema
            habilitacao_classificacao["extraido_indevidamente"] += 1
        detalhe["habilitacao"] = (
            f"golden_tem={tem_hab_golden} sistema_tem={tem_hab_sistema} "
            f"({len(resultado['exigencias_habilitacao']['itens'])} aceito(s), "
            f"{resultado['exigencias_habilitacao']['itens_rejeitados']} rejeitado(s))"
        )

        uso = resultado.get("uso_llm") or {}
        if uso.get("custo_estimado_usd") is not None:
            custos_usd.append(uso["custo_estimado_usd"])
        if uso.get("latencia_total_segundos") is not None:
            latencias_s.append(uso["latencia_total_segundos"])

        detalhes.append(detalhe)

    n_avaliados = len(editais) - pulados

    def _percentil(valores: list[float], p: float) -> float | None:
        if not valores:
            return None
        if len(valores) == 1:
            return valores[0]
        return statistics.quantiles(valores, n=100)[int(p) - 1]

    return {
        "n_no_conjunto_dourado": len(editais),
        "n_avaliados": n_avaliados,
        "n_pulados_sem_extracao_em_cache": pulados,
        "acuracia_por_campo": {
            "prazo_entrega_proposta": f"{acertos_prazo}/{n_avaliados}" if n_avaliados else None,
            "valor_estimado": f"{acertos_valor}/{n_avaliados}" if n_avaliados else None,
            "exigencias_habilitacao": habilitacao_classificacao,
        },
        "custo_por_edital_usd": {
            "total": round(sum(custos_usd), 6) if custos_usd else None,
            "media": round(statistics.mean(custos_usd), 6) if custos_usd else None,
        },
        "latencia_por_edital_segundos": {
            "p50": round(_percentil(latencias_s, 50), 2) if latencias_s else None,
            "p95": round(_percentil(latencias_s, 95), 2) if latencias_s else None,
        },
        "detalhes": detalhes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sem-llm", action="store_true", help="só métricas que não exigem LLM")
    parser.add_argument(
        "--allow-llm-calls", action="store_true",
        help="extrai os editais do golden set que ainda não têm resultado em cache (gasta cota do Gemini)",
    )
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--golden-set", default="data/golden_set.json")
    parser.add_argument("--report", default="data/cache/eval_report.json")
    args = parser.parse_args()

    cache = DiskCache(Path(args.cache_dir))
    report: dict = {"sem_llm": eval_sem_llm(cache, Path(args.cache_dir) / "report.json")}

    if not args.sem_llm:
        report["com_llm"] = eval_com_golden_set(cache, Path(args.golden_set), args.allow_llm_calls)

    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Relatório salvo em %s", args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
