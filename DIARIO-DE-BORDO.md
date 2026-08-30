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
- **Solução:** reforçar "cópia literal" não foi tentado isoladamente — mas
  ao corrigir o achado #11 (troca de modelo para `gemini-3.6-flash`), o
  mesmo edital do DER-DF passou de 0 para 3 itens aceitos (ver achado #13).
  Não dá para separar quanto veio do prompt e quanto veio do modelo mais
  capaz; ambos mudaram juntos.
- **Status:** ✅ melhora observada (não isolada). Ver achado #13.

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
- **Solução:** duas camadas. (1) Prompt reforçado com a lista exata de
  declarações genéricas encontradas aqui, como exemplos negativos. (2)
  Filtro por palavra-chave NO CÓDIGO (`_e_habilitacao_generica` em
  `extract_edital.py`) que rejeita qualquer item cujo texto contenha uma
  das frases da lista — defesa em profundidade que não depende do modelo
  obedecer o prompt. Mesmo espírito da proposta (seção 8): não usar LLM
  para o que regex resolve.
- **Status:** ✅ resolvido (prompt) + mitigado estruturalmente (filtro).
  Ver achado #13 para validação parcial contra o conjunto dourado.

### 12. Todo modelo do Gemini tem cota diária PRÓPRIA de 20/dia — inclusive os que você acabou de trocar para

- **Como apareceu:** ao tentar reprocessar o conjunto dourado com os
  prompts corrigidos, `gemini-2.5-flash` e `gemini-2.5-flash-lite` já
  estavam com a cota do dia estourada (achados #7 e a tentativa seguinte).
  Troquei para `gemini-2.0-flash` (descontinuado, a API aponta para
  `gemini-3.6-flash`), testei `gemini-3.6-flash` isoladamente — funcionou.
  Ao rodar o lote completo, um 503 (alta demanda) consumiu várias
  tentativas de retry (cada tentativa é uma requisição real, conta na
  cota) e o lote inteiro bateu em 429 de novo — desta vez com `model:
  gemini-3.6-flash` na mensagem de erro.
- **Impacto:** cada nome de modelo tem seu próprio balde de 20
  requisições/dia (não é uma cota única por conta/projeto) — mas trocar
  de modelo só compra fôlego uma vez por modelo, e retries durante uma
  instabilidade momentânea (503) consomem esse fôlego rapidamente, mesmo
  sem gerar nenhum resultado útil. "Trocar de modelo quando a cota
  acaba" é uma estratégia de retorno decrescente: cada nome novo dá 20
  chamadas, não 20 chamadas por dia para sempre.
- **Solução:** nenhuma automática. Documentado como limite estrutural do
  tier gratuito. Para reprocessar um conjunto dourado maior de forma
  confiável, a alternativa real é habilitar faturamento (billing) na
  conta do Gemini — o tier pago não tem esse teto diário — ou aceitar que
  cada rodada de validação acontece aos poucos, ao longo de vários dias.
- **Status:** ⚠️ limite estrutural da conta gratuita, sem solução dentro
  do próprio tier gratuito. Afeta diretamente a viabilidade do M5 (portão
  de CI): um gate de CI que roda a cada PR precisa de uma cota que não
  estoure em 3 requisições de teste.

### 13. Validação parcial dos achados #10 e #11: sinal positivo, mas incompleto

- **Como apareceu:** dos 7 editais do conjunto dourado, só 2 conseguiram
  ser reprocessados com os prompts corrigidos e `gemini-3.6-flash` antes
  da cota travar de novo (achado #12) — exatamente os 2 editais com os
  problemas mais graves conhecidos (DER-DF: habilitação toda rejeitada;
  Comando da Aeronáutica: já abstinha corretamente antes).
- **Resultado no DER-DF:** o prazo, que antes vinha como
  `2026-09-08T00:00:00` (a data de abertura da sessão, com confiança
  total e sem aviso), agora vem `null` com a explicação: *"O documento
  estabelece que as propostas devem ser encaminhadas até a abertura da
  sessão pública (item 3.3), mas apresenta explicitamente apenas a data
  de início da sessão de disputa de preços, sem discriminar uma data e
  horário específicos rotulados como prazo final de entrega de
  propostas."* — o modelo agora reconhece a ambiguidade do próprio
  documento em vez de escolher uma data com confiança falsa. Habilitação
  foi de 0 itens aceitos (6-7 rejeitados, cópia não-literal) para 3 itens
  aceitos e coerentes (aptidão técnico-operacional, experiência mínima de
  3 anos, escritório local) com só 1 rejeitado.
- **Resultado no Comando da Aeronáutica:** prazo e valor continuam `null`
  (correto, igual antes); habilitação foi de 1 item questionável para 0 —
  discutível se é regressão ou correção, porque o rótulo dourado deste
  edital ("exigência de amostras") já era um caso de fronteira que eu
  mesmo marquei como debatível ao montar o conjunto dourado.
- **Leitura honesta:** isto NÃO é uma confirmação estatística — são só 2
  editais, e uma resposta como null (abstenção) é estritamente melhor que
  uma data errada com confiança, mesmo quando a métrica de acurácia
  binária contra o rótulo dourado não sobe. Os outros 5 editais ainda têm
  o resultado antigo (`gemini-2.5-flash-lite`, prompts antigos) em cache,
  não reprocessados. A validação completa dos achados #10 e #11 fica
  pendente até haver cota disponível de novo.
- **Status:** 🟡 sinal positivo e qualitativamente convincente, validação
  quantitativa completa bloqueada pelo achado #12.

---

### Decisão: seguir para o M4 com validação parcial

Consultado, optei (a pedido do usuário) por seguir para o M4 em vez de
esperar o reset da cota ou habilitar faturamento. A validação
quantitativa completa dos achados #10/#11 contra os 7 editais do
conjunto dourado fica como pendência registrada, não escondida — retomar
quando houver cota (reset diário ou billing) ou ao crescer o conjunto
dourado mais adiante.

## Como ler esta lista

Dos 13 problemas registrados: **9 resolvidos ou com melhora observada**,
**3 em aberto** (heurística de escolha de PDF, cota diária por modelo sem
solução dentro do tier gratuito, e a mesma cota impedindo validação
quantitativa completa), **1 em validação parcial** (achado #13 — sinal
positivo, amostra pequena demais para confirmar). Nenhum foi escondido
atrás de um número de acurácia "limpo" — é a mesma regra que o sistema
aplica aos próprios dados (seção 8 da proposta: "não publique número de
geração se a bateria não
fechou") aplicada ao processo de construção do sistema.
