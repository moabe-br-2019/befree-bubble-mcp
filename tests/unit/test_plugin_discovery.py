from __future__ import annotations

import json
from pathlib import Path

from bubble_mcp.cli.main import main
from bubble_mcp.plugin_authoring.discovery import (
    endpoint_signature,
    export_discovery_samples,
    summarize_discovery_log,
)


def _event(
    method: str,
    url: str,
    *,
    status: int = 200,
    plugin_id: str = "1700000000000x1",
    received_at: str = "2026-11-25T10:00:00Z",
    request_body: object = None,
    response: object = None,
) -> dict[str, object]:
    return {
        "received_at": received_at,
        "kind": "discovery",
        "plugin_id": plugin_id,
        "method": method,
        "endpoint": url,
        "status": status,
        "payload": {
            "pluginId": plugin_id,
            "url": url,
            "method": method,
            "status": status,
            "requestBody": request_body,
            "responseData": response,
        },
    }


def _write_log(path: Path, events: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def _fixture_log(tmp_path: Path) -> Path:
    return _write_log(
        tmp_path / "discovery.jsonl",
        [
            _event("POST", "https://bubble.io/appeditor/write", request_body={"changes": []}, response={"ok": 1}),
            _event(
                "POST",
                "https://bubble.io/appeditor/write",
                status=400,
                received_at="2026-11-25T11:00:00Z",
                request_body={"appname": "x"},
                response={"error": "bad"},
            ),
            _event(
                "GET",
                "https://bubble.io/appeditor/get_raw_plugin?plugin_id=1&version=1",
                plugin_id="1700000000000x2",
                response={"plugin": {}},
            ),
        ],
    )


def test_endpoint_signature_replaces_ids_and_drops_query() -> None:
    assert (
        endpoint_signature("post", "https://bubble.io/appeditor/load_single_path/1700000000000x123/test/abc?x=1")
        == "POST bubble.io/appeditor/load_single_path/{id}/test/abc"
    )
    assert endpoint_signature("GET", "https://bubble.io/x/0123456789abcdef0123/y") == "GET bubble.io/x/{id}/y"
    assert endpoint_signature("GET", "https://bubble.io/x/1234567") == "GET bubble.io/x/{id}"
    assert endpoint_signature("GET", "https://bubble.io/x/12345") == "GET bubble.io/x/12345"
    assert endpoint_signature("POST", "https://api.segment.io/v1/batch") == "POST api.segment.io/v1/batch"


def test_summarize_groups_by_signature(tmp_path: Path) -> None:
    rows = summarize_discovery_log(_fixture_log(tmp_path))
    by_signature = {row["signature"]: row for row in rows}

    assert set(by_signature) == {"POST bubble.io/appeditor/write", "GET bubble.io/appeditor/get_raw_plugin"}
    write = by_signature["POST bubble.io/appeditor/write"]
    assert write["count"] == 2
    assert write["statuses"] == [200, 400]
    assert write["first_seen"] == "2026-11-25T10:00:00Z"
    assert write["last_seen"] == "2026-11-25T11:00:00Z"
    assert write["request_keys"] == ["appname", "changes"]
    assert write["response_keys"] == ["error", "ok"]


def test_summarize_filters_by_plugin_and_since(tmp_path: Path) -> None:
    log = _fixture_log(tmp_path)

    only_first = summarize_discovery_log(log, plugin_id="1700000000000x1")
    assert [row["signature"] for row in only_first] == ["POST bubble.io/appeditor/write"]

    late = summarize_discovery_log(log, since="2026-11-25T10:30:00Z")
    assert [(row["signature"], row["count"]) for row in late] == [("POST bubble.io/appeditor/write", 1)]


def test_summarize_skips_malformed_lines(tmp_path: Path) -> None:
    log = _fixture_log(tmp_path)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("not json\n\n")
    assert len(summarize_discovery_log(log)) == 2


def test_export_redacts_and_caps_samples(tmp_path: Path) -> None:
    events = [
        _event("POST", "https://bubble.io/appeditor/write", request_body={"cookie": "abc", "code": f"x{i}"})
        for i in range(7)
    ]
    log = _write_log(tmp_path / "discovery.jsonl", events)

    written = export_discovery_samples(log, tmp_path / "out")

    assert len(written) == 1
    samples = json.loads(written[0].read_text(encoding="utf-8"))
    assert samples["signature"] == "POST bubble.io/appeditor/write"
    assert len(samples["events"]) == 5
    body = samples["events"][0]["payload"]["requestBody"]
    assert body["cookie"] == "[REDACTED]"
    assert body["code"] == "x0"


def test_cli_discovery_summary_and_export(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    log = _fixture_log(tmp_path)

    assert main(["plugin", "discovery", "summary", "--log", str(log)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {row["signature"] for row in payload["endpoints"]} == {
        "POST bubble.io/appeditor/write",
        "GET bubble.io/appeditor/get_raw_plugin",
    }

    out_dir = tmp_path / "export"
    assert main(["plugin", "discovery", "export", "--log", str(log), "--out", str(out_dir)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["ok"] is True
    assert len(exported["files"]) == 2
    assert all(Path(item).exists() for item in exported["files"])


def test_export_redacts_string_bodies_and_key_value_pairs(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "discovery.jsonl",
        [
            _event(
                "POST",
                "https://bubble.io/appeditor/save",
                request_body='{"password": "hunter2", "code": "keep"}',
                response={"headers": [{"key": "X-API-Key", "value": "abc123"}, {"key": "Accept", "value": "json"}]},
            ),
            _event("POST", "https://bubble.io/appeditor/form", request_body="user=a&password=hunter2"),
        ],
    )
    written = export_discovery_samples(log, tmp_path / "out")
    text = "".join(path.read_text(encoding="utf-8") for path in written)
    assert "hunter2" not in text
    assert "abc123" not in text
    assert '"keep"' in text
    assert '"json"' in text


def test_export_marks_truncated_samples(tmp_path: Path) -> None:
    event = _event("GET", "https://bubble.io/appeditor/big", response='{"a": "cut')
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["responseTruncated"] = True
    log = _write_log(tmp_path / "discovery.jsonl", [event])
    samples = json.loads(export_discovery_samples(log, tmp_path / "out")[0].read_text(encoding="utf-8"))
    assert samples["contains_truncated"] is True
    assert "do not commit" in samples["note"]
