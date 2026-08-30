"""Cache em disco, deduplicado por numeroControlePNCP.

Cada edital ocupa três posições no cache: os metadados da listagem, o ZIP
baixado (bruto, para reprocessamento sem nova chamada de rede) e o texto
extraído por página. A extração roda só se o texto ainda não existir.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def sanitize_key(numero_controle_pncp: str) -> str:
    """numeroControlePNCP contém '/', que não é seguro em nome de arquivo."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", numero_controle_pncp)


@dataclass
class DiskCache:
    root: Path

    metadata_dir: Path = field(init=False)
    raw_dir: Path = field(init=False)
    text_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.metadata_dir = self.root / "metadata"
        self.raw_dir = self.root / "raw"
        self.text_dir = self.root / "text"
        for d in (self.metadata_dir, self.raw_dir, self.text_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- metadados da listagem -------------------------------------------------

    def has_metadata(self, key: str) -> bool:
        return (self.metadata_dir / f"{key}.json").exists()

    def write_metadata(self, key: str, record: dict[str, Any]) -> None:
        (self.metadata_dir / f"{key}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_metadata(self, key: str) -> dict[str, Any]:
        return json.loads((self.metadata_dir / f"{key}.json").read_text(encoding="utf-8"))

    # -- zip bruto ---------------------------------------------------------

    def zip_path(self, key: str, sequencial_documento: int) -> Path:
        return self.raw_dir / f"{key}__doc{sequencial_documento}.zip"

    def extract_dir(self, key: str, sequencial_documento: int) -> Path:
        return self.raw_dir / f"{key}__doc{sequencial_documento}"

    def has_zip(self, key: str, sequencial_documento: int) -> bool:
        return self.zip_path(key, sequencial_documento).exists()

    def write_zip(self, key: str, sequencial_documento: int, content: bytes) -> Path:
        p = self.zip_path(key, sequencial_documento)
        p.write_bytes(content)
        return p

    # -- texto extraído ------------------------------------------------------

    def text_path(self, key: str) -> Path:
        return self.text_dir / f"{key}.json"

    def has_text(self, key: str) -> bool:
        return self.text_path(key).exists()

    def write_text_result(self, key: str, result: dict[str, Any]) -> None:
        self.text_path(key).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_text_result(self, key: str) -> dict[str, Any]:
        return json.loads(self.text_path(key).read_text(encoding="utf-8"))

    def all_text_results(self) -> list[dict[str, Any]]:
        results = []
        for f in sorted(self.text_dir.glob("*.json")):
            results.append(json.loads(f.read_text(encoding="utf-8")))
        return results
