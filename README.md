# procurement-triage-service

Serviço de triagem de editais do PNCP (Portal Nacional de Contratações
Públicas). Não é um chatbot sobre editais: extrai os campos que decidem se
vale a pena ler um edital — prazo de entrega da proposta, exigências de
habilitação, valor estimado — com citação verificável, recusa-se a responder
quando não tem base, e ordena a fila de leitura pelo valor esperado sob a
capacidade real de um time.

Problemas reais encontrados construindo isto, e como cada um foi resolvido
(ou por que ficou em aberto): [`DIARIO-DE-BORDO.md`](DIARIO-DE-BORDO.md).

## Status

- **M1 — ingestão e corpus: concluído.** 300/300 editais processados.
- **M2 — extração com citação verificável: implementado e validado**, com
  ressalva de escala (ver "Achado real" abaixo). Verificador (14 testes) e
  orquestração (9 testes) cobertos por testes offline; validado ao vivo em
  2 editais reais.
- **M3 — conjunto dourado e harness: em andamento, correção em validação
  parcial.** 7 editais rotulados à mão (não por LLM). A primeira rodada
  com N>2 expôs 2 problemas sérios de acurácia (prazo confuso com abertura
  de sessão; habilitação super-incluindo boilerplate jurídico) — corrigidos
  no prompt e no código, mas a cota diária do Gemini (20/dia **por
  modelo**, achado novo) só deixou reprocessar 2 dos 7 editais para medir
  se a correção funcionou. Sinal qualitativo positivo nos 2, validação
  quantitativa completa pendente. Detalhes: [`DIARIO-DE-BORDO.md`](DIARIO-DE-BORDO.md).

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
  pncp_client.py    cliente HTTP com retry/backoff, paginação, timeouts
                    calibrados por endpoint
  cache.py          cache em disco deduplicado por numeroControlePNCP
  pdf_text.py       extração de texto por página; classifica cobertura
                    (texto_ok / parcialmente_escaneado / imagem_escaneada)
  run_ingest.py     CLI de ingestão (M1)
src/extract/
  schema.py         campos brutos (LLM) e finais (pós-verificação)
  verifier.py       verificação programática, sem LLM — citação existe?
                    valor bate com data/moeda parseada do trecho?
  gemini_client.py  extração por campo via Gemini, rate limit + retry
  prompts.py        um prompt por campo (prazo, valor, habilitação)
  extract_edital.py orquestra os 3 campos + divergência API x PDF
  run_extract.py    CLI de extração (M2)
scripts/
  evaluate.py       harness (M3): `--sem-llm` e com conjunto dourado
data/
  golden_set.json   conjunto dourado, rotulado à mão
  cache/            metadata/, raw/, text/ (M1), extractions/ (M2) — local, fora do git
tests/              35 testes unitários, todos sem rede
```

## Uso

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # Linux/Mac

# M1 — ingestão
python -m src.ingest.run_ingest --n 300 --data-final 20260930

# M2 — extração (precisa de GEMINI_API_KEY no .env)
python -m src.extract.run_extract --n 30

# M3 — harness
python -m scripts.evaluate --sem-llm   # sempre disponível, não chama LLM
python -m scripts.evaluate             # + conjunto dourado, usa cache existente
python -m scripts.evaluate --allow-llm-calls   # extrai o que faltar no cache
```

Rodar os testes (sem rede, sem chave de API):

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

## M2 — achado real: cota gratuita do Gemini

Rodando a extração contra editais reais (não só o smoke test), a camada
gratuita do `gemini-2.5-flash` devolveu 429 (`Too Many Requests`) e 503
(`Service Unavailable`) com frequência alta — mesmo espaçando as chamadas a
6,5s entre requisições. Um único edital (3 chamadas: prazo, valor,
habilitação) chegou a levar mais de 5 minutos por causa dos retries.

Duas decisões vieram desse achado:

1. **Espaçamento subiu de 6,5s para 12s** entre chamadas, para gastar menos
   tentativas de retry (`GEMINI_MIN_SECONDS_BETWEEN_CALLS` no `.env`).
2. **O harness (M3) não depende de rodar contra a API a cada execução.**
   `scripts/evaluate.py` lê o que já está em `data/cache/extractions/`;
   só chama o Gemini de novo com `--allow-llm-calls`, explícito. Os testes
   automatizados (35, todos offline) usam um LLM falso com respostas fixas
   para testar a lógica de verificação — não a disponibilidade da API.

**Consequência aceita:** o conjunto dourado começou com 2 editais, não os
40–60 da proposta original. A seção 7 da proposta já previa isso ("rotule
poucos primeiro, rode o harness, aprenda antes de escalar") — o harness em
si está pronto para crescer o conjunto dourado aos poucos, sem depender de
um lote grande rodar de uma vez contra uma cota que não aguenta.

## M2 — achado real: parser de data com hora antes do dia

Um edital real (DER-DF) escreve o prazo como "às 10h do dia 08 de setembro
de 2026" — a hora vem *antes* da data. O verificador original só procurava
horário *depois* da data no texto, e sempre voltava meia-noite. Corrigido
em `verifier.py` para checar os dois lados; também passou a reconhecer hora
sem minutos ("10h", não só "10:00" ou "10h00"). Coberto por teste.

## M2 — achado real: LLM não copia sempre verbatim

No mesmo edital (DER-DF), o texto-fonte tem exigências de habilitação
técnica reais e extensas (atestados de capacidade técnica, art. 67 §5º da
Lei 14.133/2021 — confirmado por leitura manual do texto extraído). O LLM
propôs 6 itens de habilitação, mas todos foram **rejeitados** pelo
verificador: o `trecho` retornado era um resumo/paráfrase, não uma cópia
literal do documento, então `citation_exists` (que exige substring exata,
após normalizar espaço) falhou para todos.

Isso é o sistema fazendo exatamente o que a regra do M2 manda — não aceitar
citação que não confere — mas também é uma **abstenção covarde** medida
pelo harness (`abstencao_covarde` em `acuracia_por_campo`): a informação
existia no documento, e o sistema devolveu vazio. Fica registrado como
limitação conhecida, não escondido: o prompt de habilitação provavelmente
precisa reforçar "cópia literal" com mais ênfase, ou aceitar correspondência
aproximada (fuzzy) em vez de substring exata — mudança a validar com mais
exemplos no conjunto dourado antes de decidir.

## M3 — harness

`scripts/evaluate.py` tem duas camadas independentes:

- **`--sem-llm`**: cobertura de texto (M1), verificabilidade de citação e
  divergência API×PDF (M2), agregadas sobre o que já está em
  `data/cache/`. Nenhuma chamada de rede — roda de graça, roda sempre.
- **Com conjunto dourado** (`data/golden_set.json`, rotulado à mão): acurácia
  por campo, custo e latência por edital (de `uso_llm` em cada extração).
  Por padrão usa só o cache; `--allow-llm-calls` extrai o que faltar.

## M3 — achado real: cota do Gemini é DIÁRIA, não por minuto

Ao tentar extrair os 5 editais novos do conjunto dourado, o `gemini-2.5-flash`
devolveu `429 RESOURCE_EXHAUSTED` com a mensagem
`GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`. Não é
limite de requisições por minuto — é **20 requisições por dia**, no total,
para esse modelo. Um único edital (3 chamadas) já consome 15% da cota
diária inteira; o lote de 5 editais (15 chamadas) não cabia de jeito
nenhum. Troquei o modelo padrão para `gemini-2.5-flash-lite`, que tem cota
diária bem maior no tier gratuito, e a extração dos 5 editais completou
normalmente depois disso. `uso_llm.modelo` agora registra qual modelo
gerou cada extração, para rastreabilidade.

## M3 — resultado real (7 editais no conjunto dourado)

| Campo | Resultado | Leitura |
|---|---|---|
| `prazo_entrega_proposta` | **2/7 (28,6%)** | Ver achado abaixo — é sério. |
| `valor_estimado` | 6/7 (85,7%) | O erro é um caso limítrofe de propósito (ver `golden_set.json`, edital do Comando do Exército): o texto tem um valor em R$ que parece boilerplate de outro edital; o sistema extraiu, o rótulo dourado diz null. Divergência de julgamento, não claramente um bug. |
| `exigencias_habilitacao` | 4 extraídos corretamente, 1 abstenção covarde, **2 extraídos indevidamente** | Ver achado abaixo. |

Custo: US$ 0,1405 total / US$ 0,0281 por edital em média (7 editais, `gemini-2.5-flash-lite`).
Latência por edital: p50 = 12,1s, p95 = 35,9s (3 chamadas de LLM por edital).

### Achado real: o modelo confunde "abertura da sessão" com "prazo da proposta"

**Este é o achado mais importante do M3 até agora**, porque prazo é o campo
que a proposta (seção 1) chama de mais caro errar: "Errar aqui é perder a
licitação." Em pelo menos 3 dos 5 editais novos, o valor extraído bate com
uma *outra* data do documento — data de abertura de sessão, ou outra data
qualquer — não com o prazo real de entrega da proposta. Exemplo concreto:
o edital da Secretaria de Administração da Paraíba diz explicitamente
"Data de Abertura: 06/03/2026 às 09:00h" logo na primeira página, e é
exatamente esse valor que o sistema devolveu — mesmo o prompt dizendo
textualmente "não confunda com data de abertura da sessão". O prazo real
(`dataEncerramentoProposta` na API) era 01/09/2026.

Isso não é um bug de parsing (o verificador confirmou a citação e a data
corretamente) — é o LLM respondendo à pergunta errada com confiança. Precisa
de mais exemplos no conjunto dourado para confirmar o padrão, e provavelmente
de um prompt mais explícito (ex.: pedir para o modelo primeiro listar todas
as datas candidatas antes de escolher, ou dar exemplos negativos).

### Achado real: o modelo super-inclui declaração jurídica genérica como "habilitação técnica"

Em 2 dos 7 editais, o sistema devolveu de 6 a 8 itens de "habilitação
técnica" que, na inspeção, são só declarações jurídicas padrão presentes em
praticamente todo edital público — cumprimento de cotas para PCD, não
emprego de menor, ausência de trabalho degradante, regras de cooperativa,
consulta ao CNEP. O prompt pede explicitamente para excluir "habilitação
jurídica ou fiscal genérica", mas o modelo incluiu mesmo assim. As citações
em si são reais (passaram no verificador — o texto existe, verbatim, no
documento), então isto não é alucinação de citação; é o modelo não
respeitando o filtro de relevância do prompt.

**Confirmei que os rótulos dourados desses 2 editais estavam certos**
(nenhum atestado de capacidade técnica de verdade nesses editais, e sim
essas declarações genéricas) — não é o meu rótulo manual que errou, é o
modelo que super-incluiu. Adicionei uma 4ª categoria no harness
(`extraido_indevidamente`) para não deixar esses casos desaparecerem da
contagem — o bug original só tinha 3 categorias e esses 2 casos sumiam da
soma sem gerar erro nem aviso.

## M3 — correção tentada, validação parcial (bloqueada por cota, não por resultado)

Depois dos dois achados acima: reforcei os dois prompts (prazo agora pede
`datas_candidatas` com rótulo antes da resposta final; habilitação lista os
exemplos exatos de boilerplate encontrados) e adicionei um filtro por
palavra-chave no código para habilitação genérica, como defesa que não
depende do modelo obedecer o prompt. Detalhes técnicos e os 3 achados de
cota que apareceram tentando *medir* a correção estão no
[`DIARIO-DE-BORDO.md`](DIARIO-DE-BORDO.md) (achados #12 e #13) — o resumo:

- `gemini-2.5-flash` e `gemini-2.5-flash-lite` têm a MESMA cota de 20
  requisições/dia, cada modelo com seu próprio balde. Troquei para
  `gemini-3.6-flash` (o 2.0 foi descontinuado); um 503 transitório gastou
  várias tentativas de retry e a cota estourou de novo antes de reprocessar
  todo o conjunto dourado.
- **Só 2 dos 7 editais foram reprocessados** antes da cota travar —
  exatamente os 2 com os problemas mais graves conhecidos.
- **Resultado nesses 2 (qualitativo, positivo, amostra pequena):** no
  DER-DF, o prazo que antes vinha `2026-09-08T00:00:00` (a data de
  abertura da sessão, com confiança total) agora vem `null`, com o modelo
  explicando que o documento só declara a abertura da sessão pública, sem
  uma data separada rotulada como prazo de entrega. Habilitação foi de 0
  itens aceitos (todos rejeitados por cópia não-literal) para 3 itens
  aceitos e tecnicamente coerentes. No Comando da Aeronáutica, prazo e
  valor continuam corretamente `null`; habilitação foi de 1 item
  questionável para 0 (discutível se é correção ou regressão — o rótulo
  dourado deste caso já era de fronteira).
- **Leitura honesta:** transformar uma resposta errada com confiança em uma
  abstenção correta com justificativa é uma melhoria real mesmo quando a
  métrica binária de acurácia não sobe — é exatamente o comportamento que
  a proposta pede ("abstenção covarde é ruim, mas alucinação com confiança
  é pior"). Mas são só 2 editais. **A validação quantitativa completa dos
  7 fica pendente** até haver cota disponível de novo (reset diário, ou
  faturamento habilitado na conta do Gemini).
