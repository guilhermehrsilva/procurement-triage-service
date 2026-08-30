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

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

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

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=3, min=3, max=90),
        reraise=True,
    )
    def extract(self, prompt: str, response_schema: type[T]) -> T:
        self._rate_limiter.wait()
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )
        if resp.parsed is None:
            raise ValueError(f"Gemini não devolveu JSON parseável: {resp.text!r}")
        return resp.parsed
