from pathlib import Path

from src.ingest.cache import DiskCache, sanitize_key


def test_sanitize_key_strips_slash():
    assert sanitize_key("34164319000174-1-000131/2024") == "34164319000174-1-000131_2024"


def test_sanitize_key_is_filesystem_safe():
    key = sanitize_key("34164319000174-1-000131/2024")
    assert "/" not in key
    assert "\\" not in key


def test_disk_cache_roundtrip(tmp_path: Path):
    cache = DiskCache(tmp_path)
    key = "abc-123"
    record = {"numeroControlePNCP": "abc/123", "valorTotalEstimado": 0.0}

    assert not cache.has_metadata(key)
    cache.write_metadata(key, record)
    assert cache.has_metadata(key)
    assert cache.read_metadata(key) == record

    assert not cache.has_text(key)
    result = {"status": "texto_ok", "num_paginas": 3}
    cache.write_text_result(key, result)
    assert cache.has_text(key)
    assert cache.read_text_result(key) == result


def test_all_text_results_collects_every_entry(tmp_path: Path):
    cache = DiskCache(tmp_path)
    cache.write_text_result("a", {"status": "texto_ok"})
    cache.write_text_result("b", {"status": "imagem_escaneada"})

    results = cache.all_text_results()
    statuses = {r["status"] for r in results}
    assert statuses == {"texto_ok", "imagem_escaneada"}
