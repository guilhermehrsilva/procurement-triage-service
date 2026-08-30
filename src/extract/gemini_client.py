"""Cliente Gemini para extração de campo único, com limitador de taxa.

A cota gratuita do Gemini é por requisições por minuto (RPM). Estourar a
cota derruba o lote inteiro se não for tratado — já vimos isso acontecer em
outros projetos (ver riscos da proposta). Por isso todo call passa por um
espaçador mínimo entre requisições e por retry com backoff em erro 429.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TypeVar

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Achado do M3: gemini-2.5-flash no tier gratuito tem cota de só 20
# requisições/DIA (não é limite por minuto — é
# "GenerateRequestsPerDayPerProjectPerModel-FreeTier", quotaValue=20). Um
# único edital (3 chamadas) já consome 15% da cota diária. gemini-2.5-flash-lite
# tem cota diária bem maior no tier gratuito.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# Preços aproximados do gemini-2.5-flash em 29/08/2026 (USD por 1M de tokens).
# Configurável porque preço de LLM muda; isto é estimativa, não fatura.
_PRECO_INPUT_POR_1M = float(os.environ.get("GEMINI_PRECO_INPUT_POR_1M_USD", "0.30"))
_PRECO_OUTPUT_POR_1M = float(os.environ.get("GEMINI_PRECO_OUTPUT_POR_1M_USD", "2.50"))

# Achado do M2: mesmo espaçando a 6,5s, a cota gratuita devolveu 429/503 com
# frequência. O retry com backoff (abaixo) resolve, mas é lento. Espaçamos
# mais para gastar menos tentativas.
_MIN_SECONDS_BETWEEN_CALLS = float(os.environ.get("GEMINI_MIN_SECONDS_BETWEEN_CALLS", "12"))


class _RateLimiter:
    """Garante um intervalo mínimo entre chamadas, entre threads."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            faltam = self._min_interval - elapsed
            if faltam > 0:
                time.sleep(faltam)
            self._last_call = time.monotonic()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ServerError):
        return True
    if isinstance(exc, ClientError):
        # 429 = cota estourada, vale esperar e tentar de novo. Outro erro de
        # cliente (400 prompt inválido, 403 chave sem permissão) não some
        # com retry — falha rápido em vez de gastar 5 tentativas à toa.
        return exc.code == 429
    return False


class GeminiFieldExtractor:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self._model = model
        self._rate_limiter = _RateLimiter(_MIN_SECONDS_BETWEEN_CALLS)
        self._n_chamadas = 0
        self._tokens_entrada = 0
        self._tokens_saida = 0
        self._latencia_total_s = 0.0

    def reset_usage(self) -> None:
        """Zera os contadores — chamar antes de processar um novo edital,
        para medir custo e latência por documento (seção 5 da proposta)."""
        self._n_chamadas = 0
        self._tokens_entrada = 0
        self._tokens_saida = 0
        self._latencia_total_s = 0.0

    def usage_snapshot(self) -> dict:
        custo_usd = (
            self._tokens_entrada / 1_000_000 * _PRECO_INPUT_POR_1M
            + self._tokens_saida / 1_000_000 * _PRECO_OUTPUT_POR_1M
        )
        return {
            "modelo": self._model,
            "n_chamadas": self._n_chamadas,
            "tokens_entrada": self._tokens_entrada,
            "tokens_saida": self._tokens_saida,
            "custo_estimado_usd": round(custo_usd, 6),
            "latencia_total_segundos": round(self._latencia_total_s, 2),
        }

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=3, min=3, max=90),
        reraise=True,
    )
    def extract(self, prompt: str, response_schema: type[T]) -> T:
        self._rate_limiter.wait()
        inicio = time.monotonic()
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
        self._latencia_total_s += time.monotonic() - inicio
        self._n_chamadas += 1
        usage = resp.usage_metadata
        if usage is not None:
            self._tokens_entrada += usage.prompt_token_count or 0
            # tokens de "pensamento" (thinking) são cobrados como saída
            self._tokens_saida += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)

        if resp.parsed is None:
            raise ValueError(f"Gemini não devolveu JSON parseável: {resp.text!r}")
        return resp.parsed
