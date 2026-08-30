# procurement-triage-service

Serviço de triagem de editais do PNCP (Portal Nacional de Contratações
Públicas). Não é um chatbot sobre editais: extrai os campos que decidem se
vale a pena ler um edital — prazo de entrega da proposta, exigências de
habilitação, valor estimado — com citação verificável, recusa-se a responder
quando não tem base, e ordena a fila de leitura pelo valor esperado sob a
capacidade real de um time.

## Status

**M1 — ingestão e corpus: concluído.** 300/300 editais processados.

## Fonte de dados

API pública do PNCP, sem autenticação:

```
GET https://pncp.gov.br/api/consulta/v1/contratacoes/proposta
    ?dataFinal=AAAAMMDD&codigoModalidadeContratacao=6&pagina=1&tamanhoPagina=50

GET https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos

GET https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos/{sequencialDocumento}
```

Verificado em 29–30/08/2026:

- 17.860 editais de Pregão Eletrônico com proposta aberta até 30/09/2026
  (1.786 páginas de 10 registros).
- Listagem leva 11 a 25 s por página — nunca chamar no ciclo de requisição.
- Endpoint de arquivos é rápido (~0,2 a 0,5 s).
- **Achado do M1:** apesar da documentação sugerir ZIP, parte dos documentos
  retornados pelo endpoint de arquivos já é PDF puro (sem contêiner ZIP). O
  cliente detecta isso pela assinatura de bytes (`PK` vs `%PDF`), não pela
  extensão.
- O primeiro edital inspecionado (`CASA DA MOEDA DO BRASIL`) tem
  `valorTotalEstimado = 0.0` na API — valor sigiloso, ausente, ou os dois.
  Medir a divergência entre esse valor e o que está escrito no PDF é uma
  entrega do M2, não um problema a esconder.

## Estrutura

```
src/ingest/
  pncp_client.py   cliente HTTP com retry/backoff, paginação, timeouts
                    calibrados por endpoint
  cache.py         cache em disco deduplicado por numeroControlePNCP
  pdf_text.py       extração de texto por página; classifica cobertura
                    (texto_ok / parcialmente_escaneado / imagem_escaneada)
  run_ingest.py     CLI de ingestão (M1)
data/cache/          metadata/, raw/ (zips e pdfs extraídos), text/ (json)
tests/                testes unitários (sem rede)
```

## Uso

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # Linux/Mac

python -m src.ingest.run_ingest --n 300 --data-final 20260930
```

Gera `data/cache/report.json` com a contagem de editais por status
(`texto_ok`, `parcialmente_escaneado`, `imagem_escaneada`,
`falha_download`, etc.) e o tempo total do lote.

Rodar os testes (sem rede):

```bash
.venv/Scripts/python -m pytest tests/ -q
```

## Relatório de cobertura (M1)

Lote de 300 editais de Pregão Eletrônico com proposta aberta até 30/09/2026
(`data-final=20260930`), processado em 29,9 min (`data/cache/report.json`):

| Status | Qtde | % |
|---|---|---|
| `texto_ok` | 290 | 96,7% |
| `imagem_escaneada` | 4 | 1,3% |
| `sem_pdf_no_pacote` | 5 | 1,7% |
| `falha_extracao_texto` | 1 | 0,3% |

**Leitura:** a esmagadora maioria dos editais rende texto extraível sem OCR.
`sem_pdf_no_pacote` são casos em que o "documento" listado pelo PNCP como
Edital não continha PDF (só planilha, ou pacote vazio) — vale investigar
manualmente antes do M2, não é necessariamente um bug de extração.
`imagem_escaneada` é o limite conhecido e aceito (seção 7 da proposta): OCR
fica para outra fase.
