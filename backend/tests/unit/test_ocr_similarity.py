from __future__ import annotations

from app.llm.ocr import content_similarity


def test_identical():
    assert content_similarity("Rechnung 44,95 EUR", "Rechnung 44,95 EUR") == 1.0


def test_whitespace_and_case_insensitive():
    assert content_similarity("Telarko  Rechnung\nMärz", "telarko rechnung märz") == 1.0


def test_disjoint():
    assert content_similarity("completely different", "Stadtwerke Abrechnung") < 0.3


def test_empty_vs_text():
    assert content_similarity("", "some text") == 0.0
    assert content_similarity("", "") == 1.0


def test_garbled_vs_clean_is_low():
    garbled = "b4x 9 ..  ---  zz*"
    clean = "Telarko Deutschland GmbH Rechnung Rechnungsnummer 4711"
    assert content_similarity(garbled, clean) < 0.4
