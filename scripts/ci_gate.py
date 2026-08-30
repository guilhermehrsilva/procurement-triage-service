"""M5: portão de qualidade em CI.

Roda os checks que NÃO dependem de LLM contra o cache fornecido — em CI,
um fixture pequeno e sintético (`tests/fixtures/cache/`), nunca o cache de
produção (local, fora do git). Compara contra o teto/piso definido em
`data/baseline_metrics.json` e sai com código de erro (e mensagem clara)
se algum limite for violado — é isso que faz o merge ser reprovado.

Por que não usa LLM: a cota gratuita do Gemini é de só 20 requisições/dia
POR MODELO (ver DIARIO-DE-BORDO.md, achado #12) — não aguenta rodar a
cada PR. A acurácia por campo contra o conjunto dourado é avaliada
manualmente com `python -m scripts.evaluate --force` quando há cota
disponível, não automaticamente em CI. O que ESTE portão garante em todo
PR, de graça e sem depender de cota: a lógica de verificação de citação
não regrediu (via os 55 testes unitários, que rodam antes deste gate) e
os tetos de custo/cobertura/rejeição sobre o que já está extraído não
pioraram.

Uso:
    python -m scripts.ci_gate --cache-dir tests/fixtures/cache
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.ingest.cache import DiskCache


def calcular_metricas(cache: DiskCache, report_path: Path) -> dict:
    extracoes = cache.all_extraction_results()

    custos = [e["uso_llm"]["custo_estimado_usd"] for e in extracoes if e.get("uso_llm")]

    hab_aceitos = sum(len(e["exigencias_habilitacao"]["itens"]) for e in extracoes)
    hab_rejeitados = sum(e["exigencias_habilitacao"]["itens_rejeitados"] for e in extracoes)
    hab_total = hab_aceitos + hab_rejeitados

    cobertura_texto_ok = None
    if report_path.exists():
        m1 = json.loads(report_path.read_text(encoding="utf-8"))
        contagem = m1.get("contagem_por_status", {})
        total = sum(contagem.values())
        if total:
            cobertura_texto_ok = contagem.get("texto_ok", 0) / total

    return {
        "n_editais": len(extracoes),
        "custo_medio_por_edital_usd": (sum(custos) / len(custos)) if custos else None,
        "taxa_rejeicao_habilitacao": (hab_rejeitados / hab_total) if hab_total else None,
        "cobertura_texto_ok": cobertura_texto_ok,
    }


def checar(metricas: dict, baseline: dict) -> list[str]:
    """Retorna a lista de violações (vazia = tudo dentro do esperado)."""
    violacoes = []

    teto_custo = baseline.get("custo_medio_maximo_por_edital_usd")
    custo = metricas["custo_medio_por_edital_usd"]
    if teto_custo is not None and custo is not None and custo > teto_custo:
        violacoes.append(f"custo médio por edital US$ {custo:.6f} > teto US$ {teto_custo:.6f}")

    teto_rejeicao = baseline.get("taxa_rejeicao_habilitacao_maxima")
    rejeicao = metricas["taxa_rejeicao_habilitacao"]
    if teto_rejeicao is not None and rejeicao is not None and rejeicao > teto_rejeicao:
        violacoes.append(f"taxa de rejeição de habilitação {rejeicao:.1%} > teto {teto_rejeicao:.1%}")

    piso_cobertura = baseline.get("cobertura_texto_minima")
    cobertura = metricas["cobertura_texto_ok"]
    if piso_cobertura is not None and cobertura is not None and cobertura < piso_cobertura:
        violacoes.append(f"cobertura de texto {cobertura:.1%} < piso {piso_cobertura:.1%}")

    return violacoes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--baseline", default="data/baseline_metrics.json")
    args = parser.parse_args()

    cache = DiskCache(Path(args.cache_dir))
    metricas = calcular_metricas(cache, Path(args.cache_dir) / "report.json")
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    print(json.dumps(metricas, ensure_ascii=False, indent=2))

    violacoes = checar(metricas, baseline)
    if violacoes:
        print("\nPORTÃO DE QUALIDADE REPROVADO:", file=sys.stderr)
        for v in violacoes:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(1)

    print("\nPortão de qualidade aprovado.")


if __name__ == "__main__":
    main()
