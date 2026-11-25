from __future__ import annotations

import pytest

from bubble_mcp.plugin_authoring.ids import allocate_id, decode_id, encode_id, max_id_in


def test_encode_decode_base52() -> None:
    assert encode_id(0) == "AAA"
    assert encode_id(11) == "AAL"
    assert encode_id(25) == "AAZ"
    assert encode_id(26) == "AAa"
    assert encode_id(52) == "ABA"
    assert decode_id("AAb") == 27
    assert decode_id("0") is None
    assert decode_id("AA1") is None
    assert decode_id("AAAA") is None
    with pytest.raises(ValueError):
        encode_id(52**3)


def test_allocate_uses_counter_as_last_issued() -> None:
    doc = {"editor_counter": 18, "plugin_elements": {"AAC": {}}}
    assert allocate_id(doc) == "AAT"
    assert doc["editor_counter"] == 19


def test_allocate_respects_ids_above_counter() -> None:
    doc = {"editor_counter": 7, "plugin_actions": {"AAD": {"return_value": {"AAQ": {}}}}}
    assert max_id_in(doc) == 16
    assert allocate_id(doc) == "AAR"
    assert doc["editor_counter"] == 17


def test_allocate_on_empty_plugin_matches_editor_first_id() -> None:
    doc: dict[str, object] = {"dependencies": {}, "human": "x", "meta_data": {}}
    assert allocate_id(doc) == "AAC"
    assert doc["editor_counter"] == 2
