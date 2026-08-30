"""Prompts de extração, um campo por vez (seção 4 da proposta).

Cada prompt pede a mesma coisa: um trecho copiado literalmente do
documento, não uma paráfrase. É essa cópia literal que o verificador
programático confere depois — sem ela não há como checar nada sem outro LLM.
"""

PRAZO_ENTREGA_PROPOSTA = """Você extrai o PRAZO FINAL de entrega/envio/recebimento das propostas de um \
edital de licitação — a data (e horário, se houver) até quando o licitante pode enviar sua proposta pelo \
sistema eletrônico.

Editais de licitação têm VÁRIAS datas diferentes, e é muito comum confundir uma com a outra. Antes de \
responder, procure no documento INTEIRO (não só a primeira página) e preencha `datas_candidatas` com toda \
data relevante que encontrar, cada uma com um rótulo do que ela representa. Rótulos comuns e como \
reconhecê-los:

- **"prazo de entrega da proposta"** (o que você precisa achar): geralmente aparece perto de frases como \
"recebimento das propostas até", "envio da proposta até", "encerramento do prazo para envio de propostas", \
numa seção tipo "DO ENVIO DA PROPOSTA" ou "DA SESSÃO PÚBLICA".
- **"abertura da sessão pública/disputa"**: frases como "Data de Abertura", "Abertura da Sessão Pública", \
"Início da Disputa de Preços". Isto NÃO é o prazo de entrega — é quando a sessão de lances começa (às vezes \
é o mesmo horário do encerramento do envio de propostas, às vezes não; não assuma que são iguais sem o \
texto confirmar).
- **"publicação do edital"**: data em que o edital foi divulgado no PNCP ou publicado.
- **"impugnação/esclarecimentos"**: prazo para pedidos de esclarecimento ou impugnação do edital — não é \
prazo de proposta.
- **"assinatura/retificação do documento"**: datas de assinatura de responsável técnico, ou de retificação \
do próprio edital — raramente é o prazo de proposta.
- **"outro"**: qualquer data que não se encaixe acima.

Só DEPOIS de listar as candidatas, escolha a que tem rótulo "prazo de entrega da proposta" como resposta \
final. Copie em `trecho` o texto EXATO do documento (verbatim, sem corrigir ortografia, sem parafrasear) que \
contém essa data — deve ser um dos trechos já listados em `datas_candidatas`. Em `valor_texto`, escreva a \
data (e hora, se houver) tal como está escrita no trecho.

Se o documento não trouxer essa informação de forma clara — mesmo depois de listar as candidatas — marque \
encontrado=false e explique o motivo em `motivo_nao_encontrado`. Não adivinhe, e não escolha "abertura da \
sessão" só porque é a data mais visível na primeira página.

DOCUMENTO (páginas marcadas com [PÁGINA n]):
{documento}
"""

VALOR_ESTIMADO = """Você extrai o VALOR TOTAL ESTIMADO da contratação de um edital de licitação — o valor em \
reais (R$) que a administração estima gastar no total com este objeto. Pode aparecer como "valor estimado", \
"valor total estimado", "valor máximo aceitável", ou em uma tabela/anexo de valores. Se o edital disser que o \
valor é sigiloso ou não divulgado, trate como não encontrado.

Copie em `trecho` o texto EXATO do documento (verbatim) que contém esse valor, e o número da página \
(`pagina`) onde ele aparece. Em `valor_texto`, escreva o valor tal como está escrito no trecho (ex.: \
"R$ 123.456,78").

Se o documento não trouxer essa informação de forma clara, marque encontrado=false e explique o motivo em \
`motivo_nao_encontrado`. Não adivinhe nem some valores de itens diferentes.

DOCUMENTO (páginas marcadas com [PÁGINA n]):
{documento}
"""

EXIGENCIAS_HABILITACAO = """Você extrai as EXIGÊNCIAS DE HABILITAÇÃO TÉCNICA de um edital de licitação — \
especificamente as que podem ELIMINAR um concorrente que não tenha experiência ou capacidade prévia \
específica: atestado(s) de capacidade técnica, registro em conselho de classe, certificações técnicas, \
comprovação de experiência anterior em objeto similar, quantitativo mínimo de contratos/equipes já \
executados.

NÃO INCLUA declarações jurídicas ou trabalhistas genéricas — elas aparecem em praticamente TODO edital \
público e não diferenciam concorrentes por capacidade técnica. Exemplos REAIS de declarações que NÃO devem \
entrar (encontrados rodando este prompt antes desta instrução existir — não repita esse erro):

- Declaração de cumprimento de cota para pessoa com deficiência / reabilitado da Previdência Social.
- Declaração de não emprego de menor de 18 anos em trabalho noturno/perigoso, ou menor de 16 anos.
- Declaração de não ter empregados em trabalho degradante ou forçado.
- Consulta/declaração sobre o Cadastro Nacional de Empresas Punidas (CNEP).
- Declarações específicas de regime de cooperativa (art. 16 da Lei 14.133/2021) ou de ME/EPP (LC 123/2006).
- Declaração genérica de que "cumpre os requisitos de habilitação" ou "concorda com as condições do edital".
- Certidões fiscais/trabalhistas padrão (CNPJ, regularidade fiscal, FGTS, CNDT) sem exigência incomum.

Essas exigências são sobre STATUS LEGAL do licitante, não sobre CAPACIDADE TÉCNICA para executar o objeto —
essa é a diferença que importa. Se restar dúvida se uma exigência é genérica ou técnica, NÃO a inclua.

Para cada exigência técnica real que encontrar, copie em `trecho` o texto EXATO do documento (verbatim) que \
a descreve, o número da página (`pagina`), e escreva em `descricao` um resumo curto (uma frase) do que ela \
exige.

Se não houver exigências de habilitação TÉCNICA (distintas das genéricas listadas acima), devolva uma lista \
vazia — isso é uma resposta válida e esperada para editais de objeto simples.

DOCUMENTO (páginas marcadas com [PÁGINA n]):
{documento}
"""


def build_document_context(pages: list[dict]) -> str:
    """Monta o texto completo do edital com marcadores de página."""
    partes = []
    for p in pages:
        partes.append(f"[PÁGINA {p['page']}]\n{p['text']}")
    return "\n\n".join(partes)
