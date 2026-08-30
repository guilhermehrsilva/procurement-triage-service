"""Cliente HTTP para a API pública do PNCP.

A consulta de listagem é lenta (19 a 25 s por página de 10 registros, medido
em 29/08/2026) e não tem SLA documentado. O endpoint de arquivos é rápido
(~0,2 s). Por isso os dois usam timeouts e políticas de retry diferentes, e a
listagem nunca deve ser chamada dentro de um ciclo de requisição de usuário.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

CONSULTA_BASE_URL = "https://pncp.gov.br/api/consulta/v1"
ARQUIVOS_BASE_URL = "https://pncp.gov.br/pncp-api/v1"
ARQUIVOS_META_BASE_URL = "https://pncp.gov.br/api/pncp/v1"

# Pregão Eletrônico. Ver seção 3 da proposta.
MODALIDADE_PREGAO_ELETRONICO = 6

# tamanhoPagina mínimo aceito pela API é 10; valores menores retornam HTTP 400.
TAMANHO_PAGINA_MINIMO = 10

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException)


def _is_retryable_status(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class PNCPClient:
    """Cliente fino sobre a API de consulta do PNCP, com retry e timeouts
    calibrados para a latência observada de cada endpoint."""

    def __init__(
        self,
        listagem_timeout: float = 40.0,
        arquivo_timeout: float = 30.0,
    ) -> None:
        self._listagem_client = httpx.Client(timeout=listagem_timeout)
        self._arquivo_client = httpx.Client(timeout=arquivo_timeout, follow_redirects=True)

    def close(self) -> None:
        self._listagem_client.close()
        self._arquivo_client.close()

    def __enter__(self) -> "PNCPClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    def list_contratacoes_page(
        self,
        data_final: str,
        pagina: int,
        codigo_modalidade: int = MODALIDADE_PREGAO_ELETRONICO,
        tamanho_pagina: int = 50,
    ) -> dict[str, Any]:
        """Busca uma página de contratações com proposta em aberto.

        data_final: string AAAAMMDD.
        """
        if tamanho_pagina < TAMANHO_PAGINA_MINIMO:
            raise ValueError(
                f"tamanhoPagina mínimo é {TAMANHO_PAGINA_MINIMO}; recebido {tamanho_pagina}"
            )
        resp = self._listagem_client.get(
            f"{CONSULTA_BASE_URL}/contratacoes/proposta",
            params={
                "dataFinal": data_final,
                "codigoModalidadeContratacao": codigo_modalidade,
                "pagina": pagina,
                "tamanhoPagina": tamanho_pagina,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def iter_contratacoes(
        self,
        data_final: str,
        codigo_modalidade: int = MODALIDADE_PREGAO_ELETRONICO,
        tamanho_pagina: int = 50,
        max_registros: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Itera contratações página a página até o fim ou até max_registros."""
        pagina = 1
        emitidos = 0
        while True:
            body = self.list_contratacoes_page(
                data_final, pagina, codigo_modalidade, tamanho_pagina
            )
            registros = body.get("data", [])
            if not registros:
                return
            for r in registros:
                yield r
                emitidos += 1
                if max_registros is not None and emitidos >= max_registros:
                    return
            if body.get("paginasRestantes", 0) <= 0:
                return
            pagina += 1

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def get_arquivos_metadata(self, cnpj: str, ano: int, sequencial: int) -> list[dict[str, Any]]:
        """Lista os documentos disponíveis (edital, relação de itens, etc.)."""
        resp = self._arquivo_client.get(
            f"{ARQUIVOS_META_BASE_URL}/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
        )
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def download_arquivo(self, cnpj: str, ano: int, sequencial: int, sequencial_documento: int) -> bytes:
        """Baixa o pacote (ZIP) de um documento específico."""
        url = (
            f"{ARQUIVOS_BASE_URL}/orgaos/{cnpj}/compras/{ano}/{sequencial}"
            f"/arquivos/{sequencial_documento}"
        )
        resp = self._arquivo_client.get(url)
        resp.raise_for_status()
        return resp.content
