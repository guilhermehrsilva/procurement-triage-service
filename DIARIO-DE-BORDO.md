# Diário de bordo

Registro cronológico de problemas reais encontrados construindo este
projeto, e como cada um foi resolvido (ou por que ficou em aberto). O
README documenta o estado atual do sistema; este arquivo documenta a
jornada — para não perder, ao final do projeto, o conhecimento de quais
dificuldades apareceram e que caminho resolveu cada uma. É também a
resposta pronta para "me conta um problema que você resolveu" numa
entrevista.

Formato de cada entrada: **Problema** → **Como apareceu** → **Impacto** →
**Solução** → **Status**.

---

## M1 — Ingestão e corpus

### 1. Nem todo documento do endpoint de arquivos é ZIP

- **Como apareceu:** smoke test com 8 editais reais; 2 de 8 falharam na
  descompactação.
- **Impacto:** a documentação do PNCP sugere que o endpoint de arquivos
  sempre devolve um ZIP. Na prática, uma parte relevante dos documentos
  (`file` mostrou `PDF document`, não `Zip archive`) já vem como PDF puro.
  Sem tratar isso, ~25% dos editais do smoke test já falhavam antes de
  extrair uma única página.
- **Solução:** `extract_documents()` em [`pdf_text.py`](src/ingest/pdf_text.py)
  detecta o tipo pela assinatura de bytes (`PK` = ZIP, `%PDF` = PDF puro),
  não pela extensão do arquivo salvo.
- **Status:** ✅ resolvido. Commit `b65ff56`.

### 2. Download pode travar indefinidamente com resposta lenta

- **Como apareceu:** rodando o mesmo smoke test, o 7º edital ficou "parado"
  por mais de 3 minutos sem nenhum erro — o processo não travou, só não
  progredia.
- **Impacto:** `httpx.Timeout` se aplica por operação de rede (connect,
  ler *um* chunk), não à duração total da resposta. Um servidor que
  entrega bytes devagar mas sem parar nunca estoura esse timeout. Numa
  ingestão de 300 editais, um único documento assim travaria o lote
  inteiro por tempo indefinido.
- **Solução:** `download_arquivo()` em
  [`pncp_client.py`](src/ingest/pncp_client.py) passou a usar streaming
  (`client.stream(...)` + `iter_bytes()`) com um prazo de parede total
  (`DownloadDeadlineExceeded`, 60s por padrão), verificado a cada chunk —
  independente de quão rápido cada chunk individual chega.
- **Status:** ✅ resolvido. Commit `9f9b8fc`.

### 3. A heurística de escolha do PDF do edital às vezes pega o arquivo errado

- **Como apareceu:** procurando candidatos pequenos para o conjunto
  dourado do M3, encontrei editais de "1 página" — na inspeção, o
  conteúdo era a *Relação de Itens*, não o edital de verdade.
- **Impacto:** `pick_edital_pdf()` escolhe pelo nome conter "edital"; se
  nenhum arquivo do pacote tiver esse nome, cai para o maior arquivo. Isso
  falha quando o edital de verdade tem nome genérico (ex.: `doc1.pdf`) e é
  *menor* que a relação de itens (que pode ter páginas e mais páginas de
  tabela). O sistema processaria o documento errado sem nenhum erro
  visível — silenciosamente, o campo extraído viria de outro documento.
- **Solução:** ainda **não corrigido**. Mitigação atual: ao montar o
  conjunto dourado, filtro por nome de arquivo e leio o conteúdo antes de
  confiar no rótulo `texto_ok`. Correção futura: preferir arquivos cujo
  `tipoDocumentoNome` (metadado do endpoint de arquivos, não do ZIP) diga
  "Edital", e/ou verificar se o texto do PDF escolhido contém marcadores
  típicos de edital ("PREGÃO ELETRÔNICO", "PROPOSTA") antes de aceitar.
- **Status:** ⚠️ risco conhecido, documentado, não corrigido.

---

## M2 — Extração com citação verificável

### 4. Cota gratuita do Gemini: 429/503 frequentes mesmo espaçando chamadas

- **Como apareceu:** smoke test de 2 editais (6 chamadas) levou mais de 5
  minutos por causa de retries em 429 e 503.
- **Impacto:** sem tratar isso, qualquer lote maior que uns poucos editais
  falharia ou demoraria horas.
- **Solução (parcial, ver achado #7):** `gemini_client.py` ganhou um
  limitador de taxa (`_RateLimiter`, espaçamento mínimo entre chamadas) e
  retry com backoff exponencial só para erros que valem a pena re-tentar
  (429 e 5xx — não 400/403, que falham rápido). Espaçamento subiu de 6,5s
  para 12s depois de medir que 6,5s ainda gerava muito 429.
- **Status:** ✅ mitigado (ver achado #7 para a causa raiz completa).

### 5. Parser de data perdia o horário quando ele vem antes da data

- **Como apareceu:** rodando a extração no edital real do DER-DF, o
  sistema devolveu `2026-09-08T00:00:00` para um trecho que dizia
  literalmente "às 10h do dia 08 de setembro de 2026" — a data certa, hora
  errada (sempre meia-noite).
- **Impacto:** o parser original (`_com_hora_proxima` em
  [`verifier.py`](src/extract/verifier.py)) só procurava um horário
  *depois* da posição da data no texto. Quando a hora vem antes (comum em
  português: "às Xh do dia Y"), o horário real era descartado
  silenciosamente. Também faltava reconhecer hora sem minutos ("10h", só
  "10:00" ou "10h00" eram reconhecidos).
- **Solução:** o parser passou a checar uma janela antes *e* depois da
  data, e a reconhecer "Nh" sozinho. Coberto por teste
  (`test_parse_dates_hora_antes_da_data`).
- **Status:** ✅ resolvido. Commit `2514e6e`.

### 6. LLM nem sempre copia a citação verbatim

- **Como apareceu:** o mesmo edital do DER-DF tem exigências de
  habilitação técnica reais e extensas (confirmei lendo o texto extraído
  manualmente, grep por "atestado"). O LLM propôs 6 itens; o verificador
  rejeitou todos os 6, porque o `trecho` devolvido era resumo/paráfrase,
  não cópia literal do documento.
- **Impacto:** o sistema fez exatamente o que a regra do M2 manda (não
  aceitar citação que não confere) — mas o resultado prático é uma
  **abstenção covarde**: a informação existia no documento e o sistema
  devolveu vazio.
- **Solução:** ainda **não corrigido**. É uma limitação registrada, não
  escondida. Caminhos a testar depois: reforçar "cópia literal" no prompt
  com mais ênfase/exemplos, ou trocar o `citation_exists` de substring
  exata para uma correspondência aproximada (fuzzy) com um limiar mínimo —
  mas isso precisa de mais exemplos no conjunto dourado antes de decidir,
  para não trocar uma abstenção covarde por uma citação alucinada aceita
  por engano.
- **Status:** ⚠️ risco conhecido, documentado, não corrigido.

### 7. A cota do Gemini é DIÁRIA (20 req/dia), não por minuto

- **Como apareceu:** tentando extrair 5 editais novos para o conjunto
  dourado (15 chamadas), o erro 429 trouxe a mensagem completa:
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`. Não
  era mais um problema de espaçamento — era um teto fixo de 20
  requisições por dia inteiro, para o modelo `gemini-2.5-flash`.
- **Impacto:** um único edital (3 chamadas) já consome 15% da cota diária
  inteira. Nenhum ajuste de espaçamento ou retry resolve um limite diário
  — só esperar o próximo dia, ou usar outro modelo.
- **Solução:** troquei o modelo padrão para `gemini-2.5-flash-lite`
  (`GEMINI_MODEL` no `.env`), que tem cota diária bem maior no tier
  gratuito. Também passei a registrar `uso_llm.modelo` em cada extração,
  para saber depois qual modelo gerou qual resultado.
- **Status:** ✅ mitigado (achado #4 era sintoma disso). Commit `7a551c5`.

### 8. Testes não podem depender da API responder

- **Como apareceu:** depois dos achados #4 e #7, ficou claro que rodar
  qualquer suíte de testes contra a API ao vivo era não-confiável por
  design (cota curta e instável).
- **Impacto:** sem isso, cada mudança no verificador ou na orquestração
  exigiria gastar cota (finita, diária) só para confirmar que a lógica
  funciona.
- **Solução:** `tests/test_extract_edital.py` usa um `FakeLLM` com
  respostas fixas, testando a lógica de verificação (citação existe? valor
  bate com o trecho? item rejeitado é contado?) sem nenhuma chamada de
  rede. `tests/test_evaluate.py` faz o mesmo para o harness, com cache
  fake em `tmp_path`.
- **Status:** ✅ resolvido. Commits `4a98de2` e (harness) `3129735`.

---

## M3 — Conjunto dourado e harness

### 9. Bug no harness escondia 2 de 7 resultados reais

- **Como apareceu:** ao crescer o conjunto dourado de 2 para 7 editais, a
  soma das categorias de habilitação (`extraido_corretamente` +
  `abstencao_correta` + `abstencao_covarde`) dava 5, não 7 — 2 editais
  sumiam da contagem sem gerar erro.
- **Impacto:** o harness só classificava 3 dos 4 desfechos possíveis
  (golden×sistema, cada um True/False). O caso "golden diz que não há
  exigência, sistema encontrou itens" não tinha categoria — os 2 casos
  reais desapareciam silenciosamente, escondendo sinal real.
- **Solução:** adicionada a 4ª categoria (`extraido_indevidamente`) em
  `scripts/evaluate.py`.
- **Status:** ✅ resolvido. Revelou o achado #11. Commit `8de5a3b`.

### 10. O modelo confunde "abertura da sessão" com "prazo de entrega da proposta"

- **Como apareceu:** medindo acurácia do campo `prazo_entrega_proposta`
  contra os 7 rótulos dourados: 2/7 (28,6%). Investigando os erros, o
  valor extraído batia com *outra* data do documento — no caso mais claro
  (Secretaria de Administração da Paraíba), com a "Data de Abertura:
  06/03/2026 às 09:00h" da primeira página, não com o prazo real
  (01/09/2026, confirmado pela API).
- **Impacto:** este é o campo que a proposta do projeto chama de mais caro
  errar ("errar aqui é perder a licitação"). Uma acurácia de 28,6% nele,
  sem o time saber, seria pior que não ter o campo — decisões tomadas
  sobre um prazo errado com aparência de certeza.
- **Solução:** ainda **não corrigido**. O prompt já pede explicitamente
  para não confundir os dois; o modelo confunde mesmo assim. Caminhos a
  testar: pedir para o modelo listar todas as datas candidatas antes de
  escolher (chain-of-thought forçado no schema), dar exemplos negativos no
  prompt, ou usar um modelo mais capaz só para este campo (o custo por
  chamada é baixo o suficiente para justificar).
- **Status:** ⚠️ risco conhecido, medido, não corrigido. É o achado mais
  importante do projeto até agora.

### 11. O modelo super-inclui declaração jurídica genérica como "habilitação técnica"

- **Como apareceu:** depois de corrigir o bug #9, os 2 casos que haviam
  sumido da contagem mostraram 6–8 itens de habilitação cada. Inspecionei
  as citações (que passaram no verificador — são reais, verbatim) e são
  todas declarações jurídicas padrão (cota PCD, não emprego de menor,
  regras de cooperativa, consulta ao CNEP), não exigências técnicas
  diferenciadoras.
- **Impacto:** o prompt pede explicitamente para excluir "habilitação
  jurídica ou fiscal genérica"; o modelo inclui mesmo assim. Isso não é
  alucinação (a citação existe de verdade no documento) — é o filtro de
  relevância do prompt não sendo respeitado. Um time lendo esses "6 itens
  de habilitação técnica" perderia tempo achando que a empresa precisa
  cumprir requisitos que na verdade são boilerplate presente em qualquer
  edital.
- **Antes de aceitar o achado, verifiquei se o erro não era meu**: reli
  manualmente os dois editais procurando "atestado", "capacidade técnica",
  "qualificação" — confirmei que não há exigência técnica real neles. O
  rótulo dourado estava certo; o problema é do modelo.
- **Solução:** ainda **não corrigido**. Caminho a testar: listar
  explicitamente, no prompt, exemplos do que NÃO conta (a lista de
  declarações genéricas encontradas aqui é um ótimo ponto de partida).
- **Status:** ⚠️ risco conhecido, medido, não corrigido.

---

## Como ler esta lista

Dos 11 problemas registrados: **7 resolvidos**, **4 em aberto e
documentados**. Nenhum foi escondido atrás de um número de acurácia
"limpo" — é a mesma regra que o sistema aplica aos próprios dados
(seção 8 da proposta: "não publique número de geração se a bateria não
fechou") aplicada ao processo de construção do sistema.
