from src.ingest.pdf_text import PageText, classify_extraction


def _page(text: str, n: int = 1) -> PageText:
    return PageText(page_number=n, text=text)


def test_classify_no_pages():
    assert classify_extraction([]) == "sem_paginas"


def test_classify_all_pages_have_text():
    pages = [_page("conteúdo relevante " * 5, n) for n in range(1, 4)]
    assert classify_extraction(pages) == "texto_ok"


def test_classify_no_page_has_text():
    pages = [_page("", n) for n in range(1, 4)]
    assert classify_extraction(pages) == "imagem_escaneada"


def test_classify_partial_text():
    pages = [_page("texto suficiente para contar " * 3, 1), _page("", 2), _page("", 3), _page("", 4)]
    assert classify_extraction(pages) == "parcialmente_escaneado"
