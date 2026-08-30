"""Configuração compartilhada entre a ingestão, a extração e o serviço.

Orçamento explícito (seção 4/6 da proposta, M4: "custo máximo por edital e
timeout por requisição"). Configurável por variável de ambiente, com
valores padrão conservadores para o tier gratuito do Gemini.
"""

from __future__ import annotations

import os

CACHE_DIR = os.environ.get("CACHE_DIR", "data/cache")

# Custo estimado (não a fatura real) acima do qual a extração de um edital
# para de chamar o LLM e devolve os campos restantes como null com motivo
# "orçamento excedido", em vez de continuar gastando.
CUSTO_MAX_USD_POR_EDITAL = float(os.environ.get("CUSTO_MAX_USD_POR_EDITAL", "0.05"))

# Tempo de parede (soma da latência das chamadas de LLM já feitas para o
# mesmo edital) acima do qual os campos restantes viram null com motivo
# "timeout excedido", em vez de continuar chamando o LLM.
TIMEOUT_EXTRACAO_SEGUNDOS = float(os.environ.get("TIMEOUT_EXTRACAO_SEGUNDOS", "60"))
