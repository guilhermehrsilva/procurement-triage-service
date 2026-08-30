"""Prompts de extração, um campo por vez (seção 4 da proposta).

Cada prompt pede a mesma coisa: um trecho copiado literalmente do
documento, não uma paráfrase. É essa cópia literal que o verificador
programático confere depois — sem ela não há como checar nada sem outro LLM.
"""

PRAZO_ENTREGA_PROPOSTA = """Você extrai o PRAZO FINAL de entrega/envio/recebimento das propostas de um \
edital de licitação — a data (e horário, se houver) até quando o licitante pode enviar sua proposta pelo \
sistema eletrônico. Não confunda com data de abertura da sessão, data de publicação do edital, ou prazo \
de impugnação/esclarecimentos.

Copie em `trecho` o texto EXATO do documento (verbatim, sem corrigir ortografia, sem parafrasear) que contém \
essa data, e o número da página (`pagina`) onde ele aparece. Em `valor_texto`, escreva a data (e hora, se \
houver) tal como está escrita no trecho.

Se o documento não trouxer essa informação de forma clara, marque encontrado=false e explique o motivo em \
`motivo_nao_encontrado`. Não adivinhe.

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

EXIGENCIAS_HABILITACAO = """Você extrai as EXIGÊNCIAS DE HABILITAÇÃO TÉCNICA de um edital de licitação — em \
especial atestado(s) de capacidade técnica, registro em conselho de classe, certificações e comprovações de \
experiência anterior que o licitante precisa apresentar para não ser desclassificado. Não inclua exigências \
puramente de habilitação jurídica ou fiscal genérica (CNPJ, certidões de regularidade fiscal padrão), a menos \
que sejam incomuns ou restritivas.

Para cada exigência relevante que encontrar, copie em `trecho` o texto EXATO do documento (verbatim) que a \
descreve, o número da página (`pagina`), e escreva em `descricao` um resumo curto (uma frase) do que ela exige.

Se não houver exigências de habilitação técnica além do padrão, devolva uma lista vazia.

DOCUMENTO (páginas marcadas com [PÁGINA n]):
{documento}
"""


def build_document_context(pages: list[dict]) -> str:
    """Monta o texto completo do edital com marcadores de página."""
    partes = []
    for p in pages:
        partes.append(f"[PÁGINA {p['page']}]\n{p['text']}")
    return "\n\n".join(partes)
