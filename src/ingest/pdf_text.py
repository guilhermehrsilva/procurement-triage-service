"""Extração de texto por página de um PDF de edital.

Um edital que renderiza texto vazio (ou quase) não é um bug do extrator —
é, quase sempre, PDF de imagem escaneada. A M1 registra isso como limite
conhecido (seção 7 da proposta), não tenta OCR.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Abaixo disso, uma página é tratada como "sem texto útil" (cabeçalhos e
# rodapés isolados não contam como conteúdo extraído).
MIN_CHARS_PARA_TEXTO_UTIL = 20


@dataclass
class PageText:
    page_number: int  # 1-indexado
    text: str
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.char_count = len(self.text)


def extract_documents(downloaded_path: Path, dest_dir: Path) -> list[Path]:
    """Extrai os PDFs de um documento baixado do PNCP.

    A API de arquivos nem sempre devolve ZIP apesar do que a documentação
    sugere: em parte dos casos observados (achado do M1), o corpo da
    resposta já é um PDF puro. Detectamos pela assinatura de bytes, não pela
    extensão do arquivo salvo.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    header = downloaded_path.read_bytes()[:4]
    if header.startswith(b"PK"):
        with zipfile.ZipFile(downloaded_path) as zf:
            zf.extractall(dest_dir)
        return sorted(dest_dir.rglob("*.pdf"))
    if header.startswith(b"%PDF"):
        target = dest_dir / (downloaded_path.stem + ".pdf")
        target.write_bytes(downloaded_path.read_bytes())
        return [target]
    return []


def pick_edital_pdf(pdf_paths: list[Path]) -> Path | None:
    """Escolhe o PDF do edital em si, não anexos como relação de itens.

    Heurística: nome contém "edital"; se nenhum bater, usa o maior arquivo
    (o edital principal costuma ser bem maior que anexos de itens).
    """
    if not pdf_paths:
        return None
    for p in pdf_paths:
        if "edital" in p.stem.lower():
            return p
    return max(pdf_paths, key=lambda p: p.stat().st_size)


def extract_pages(pdf_path: Path) -> list[PageText]:
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(PageText(page_number=i, text=text))
    return pages


def classify_extraction(pages: list[PageText]) -> str:
    """Classifica o resultado para o relatório de cobertura (seção 5)."""
    if not pages:
        return "sem_paginas"
    paginas_uteis = sum(1 for p in pages if p.char_count >= MIN_CHARS_PARA_TEXTO_UTIL)
    fracao = paginas_uteis / len(pages)
    if fracao == 0:
        return "imagem_escaneada"
    if fracao < 0.5:
        return "parcialmente_escaneado"
    return "texto_ok"
