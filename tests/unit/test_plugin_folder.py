from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from bubble_mcp.plugin_authoring.folder import (
    SECRETS_FILE,
    assemble,
    canonical_json,
    explode,
    read_folder,
    slugify,
    write_folder,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "mcp_test_plugin.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _doc_with_code() -> dict:
    return {
        "dependencies": {"plugin_api_version": "4", "use_jquery": True},
        "editor_counter": 5,
        "human": "T",
        "meta_data": {"name": "T", "categories": [], "description": None, "license": None},
        "html_header": {"snippet": "<script>x</script>", "params": {}},
        "plugin_elements": {
            "AAC": {
                "display": "a/b é ",
                "code": {
                    "initialize": {"fn": "function(instance, context) {\n}"},
                    "update": {"fn": "function(i, p, c) {\n\tx();\n}\n", "add_debugger": True},
                },
                "headers": {"snippet": "<style>.x{}</style>"},
                "states": {"AAD": {"name": "s", "initialization_code": {"fn": "function(p, c) {}"}}},
                "actions": {"AAE": {"caption": "go now", "code": {"fn": "function(i, p, c) {}"}}},
                "fields": {},
                "default_val_float": 1.5,
            }
        },
        "plugin_actions": {
            "AAF": {
                "display": "a/b é ",
                "type": "server_side",
                "code": {
                    "client": {"fn": "function(properties, context) {}"},
                    "server": {"fn": "async function(properties, context) {}", "invalid": False},
                    "package": {"fn": ""},
                },
                "return_value": {"AAQ": {"name": "r", "rank": 0, "value": "text"}},
            }
        },
    }


def test_round_trip_fixture() -> None:
    doc = _fixture()
    files, secrets = explode(copy.deepcopy(doc))
    assert assemble(files, secrets) == doc


def test_code_files_keep_exact_text() -> None:
    doc = _doc_with_code()
    files, secrets = explode(copy.deepcopy(doc))
    assert files["elements/AAC-a-b-e/initialize.js"] == "function(instance, context) {\n}"
    assert files["elements/AAC-a-b-e/update.js"] == "function(i, p, c) {\n\tx();\n}\n"
    assert files["actions/AAF-a-b-e/package.json"] == ""
    assert files["elements/AAC-a-b-e/header.html"] == "<style>.x{}</style>"
    assert files["elements/AAC-a-b-e/states/AAD.init.js"] == "function(p, c) {}"
    assert files["elements/AAC-a-b-e/actions/AAE-go-now.js"] == "function(i, p, c) {}"
    assert files["shared/header.html"] == "<script>x</script>"
    assert "fn" not in json.loads(files["elements/AAC-a-b-e/element.json"])["code"]["update"]
    assert assemble(files, secrets) == doc


def test_unsafe_and_shared_names_get_separate_dirs() -> None:
    assert slugify("a/b é ") == "a-b-e"
    assert slugify("   ") == "x"
    files, _ = explode(_doc_with_code())
    plugin = json.loads(files["plugin.json"])
    assert plugin["plugin_elements"] == {"AAC": "elements/AAC-a-b-e"}
    assert plugin["plugin_actions"] == {"AAF": "actions/AAF-a-b-e"}


def test_categories_list_and_object_and_empty_dicts_preserved() -> None:
    for categories in ([], {"0": "analytics"}):
        doc = _doc_with_code()
        doc["meta_data"]["categories"] = categories
        doc["plugin_elements"]["AAC"]["events"] = {}
        assert assemble(*explode(copy.deepcopy(doc))) == doc
    empty = {"dependencies": {}, "human": "e", "meta_data": {"name": "e"}, "plugin_elements": {}}
    assert assemble(*explode(copy.deepcopy(empty))) == empty


def test_unknown_keys_pass_through() -> None:
    doc = _doc_with_code()
    doc["plugin_api"] = {
        "human": "API",
        "calls": {"AAG": {"name": "c", "wrap_error": True, "0": "A", "1": "A", "2": "a", "method": "delete_method"}},
    }
    doc["dependencies"]["alpha_opt_in"] = True
    assert assemble(*explode(copy.deepcopy(doc))) == doc


def test_secrets_split_out_of_mirrored_files(tmp_path: Path) -> None:
    doc = _doc_with_code()
    doc["plugin_api"] = {
        "human": "API",
        "auth": "private_key_header",
        "private_key": "hunter2",
        "private_key_test": "hunter3",
        "shared_headers": {"AAH": {"key": "Accept", "value": "application/json", "private": False}},
        "calls": {
            "AAG": {
                "name": "c",
                "headers": {"AAI": {"key": "X-Key", "value": "hunter4", "visibility": "secret"}},
                "url_params": {"AAJ": {"key": "id", "value": "1", "private": True}},
            }
        },
    }
    write_folder(tmp_path, doc)
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.relative_to(tmp_path).as_posix() != SECRETS_FILE:
            text = path.read_text(encoding="utf-8")
            assert "hunter" not in text, path
    assert ".bubble-plugin/secrets.json" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert read_folder(tmp_path) == doc


def test_write_folder_removes_stale_files_only(tmp_path: Path) -> None:
    doc = _doc_with_code()
    write_folder(tmp_path, doc)
    (tmp_path / "notes.md").write_text("mine", encoding="utf-8")
    del doc["plugin_elements"]["AAC"]["actions"]
    write_folder(tmp_path, doc)
    assert not (tmp_path / "elements/AAC-a-b-e/actions/AAE-go-now.js").exists()
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "mine"
    assert read_folder(tmp_path) == doc


def test_new_dir_gets_allocated_id(tmp_path: Path) -> None:
    doc = _doc_with_code()
    write_folder(tmp_path, doc)
    src = tmp_path / "elements/AAC-a-b-e"
    dst = tmp_path / "elements/NEW-widget"
    dst.mkdir()
    for file in src.rglob("*"):
        if file.is_file():
            target = dst / file.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.read_text(encoding="utf-8"), encoding="utf-8")
    renames: dict[str, str] = {}
    rebuilt = read_folder(tmp_path, new_ids=renames)
    assert renames == {"elements/NEW-widget": "AAR"}
    copied = rebuilt["plugin_elements"]["AAR"]
    # Inner ids of a copied element are renewed too, so they cannot collide with the source.
    assert set(copied["states"]) == {"AAS"}
    assert set(copied["actions"]) == {"AAT"}
    assert copied["states"]["AAS"]["initialization_code"]["fn"] == "function(p, c) {}"
    assert copied["actions"]["AAT"]["code"]["fn"] == "function(i, p, c) {}"
    assert rebuilt["editor_counter"] == 19
    assert rebuilt["plugin_elements"]["AAC"] == doc["plugin_elements"]["AAC"]


def test_canonical_json_is_sorted_and_newline_terminated() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{\n  "a": "é",\n  "b": 1\n}\n'


@pytest.mark.skipif(not os.environ.get("BUBBLE_MCP_PLUGIN_CORPUS"), reason="set BUBBLE_MCP_PLUGIN_CORPUS")
def test_corpus_round_trip(tmp_path: Path) -> None:
    corpus = Path(os.environ["BUBBLE_MCP_PLUGIN_CORPUS"])
    failures = []
    for index, path in enumerate(sorted(corpus.glob("*.json"))):
        doc = json.loads(path.read_text(encoding="utf-8"))
        folder = tmp_path / str(index)
        write_folder(folder, doc)
        if read_folder(folder) != doc:
            failures.append(path.name)
    assert failures == []


def test_unflagged_old_style_headers_are_secret_and_echoes_reported() -> None:
    from bubble_mcp.plugin_authoring.folder import find_secret_echoes

    doc = _doc_with_code()
    doc["plugin_api"] = {
        "human": "API",
        "calls": {
            "AAG": {
                "name": "c",
                "headers": {"0": {"key": "Authorization", "value": "Bearer abcdefgh123"}},
                "url_params": {"AAJ": {"key": "id", "value": "1", "visibility": "public"}},
                "types": '{"x": {"sample_value": "Bearer abcdefgh123"}}',
            }
        },
    }
    files, secrets = explode(copy.deepcopy(doc))
    mirrored = json.loads(files["plugin.json"])
    del mirrored["plugin_api"]["calls"]["AAG"]["types"]
    assert "abcdefgh123" not in json.dumps(mirrored)
    assert any(entry["path"][-3:] == ["headers", "0", "value"] for entry in secrets["values"])
    assert assemble(files, secrets) == doc
    assert find_secret_echoes(doc) == ["plugin_api/calls/AAG/types"]
