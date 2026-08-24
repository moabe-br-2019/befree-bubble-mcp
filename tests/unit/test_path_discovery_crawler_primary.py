"""A crawler-index must work as a primary data source when no .bubble/consolelog exists.

Regression: free-plan apps get 401 from the .bubble export endpoint, so context detection
falls back to the editor crawler. PathDiscovery only used the crawler to ENRICH an existing
data source, so with crawler-only profiles every aria-runtime tool failed with
"No app data source found" and no context could be resolved.
"""

from __future__ import annotations

import json


CRAWLER_INDEX = {
    "appId": "crawler-only-app",
    "pages": [
        {
            "id": "bTGbC",
            "name": "index",
            "rootId": "bTGYf",
            "elements": {"bAAA1": {"id": "bAAA1", "name": "Button A"}},
            "workflows": {},
        }
    ],
    "reusables": [],
    "backendWorkflows": [],
    "apiConnectorCalls": [
        {"collectionId": "col1", "collectionName": "My APIs", "callId": "call1", "callName": "Get Products"}
    ],
}


def test_crawler_index_is_used_as_primary_data_source(tmp_path) -> None:
    from bubble_mcp.aria_runtime.bubble_sdk import PathDiscovery

    crawler_path = tmp_path / "crawler-index.json"
    crawler_path.write_text(json.dumps(CRAWLER_INDEX), encoding="utf-8")

    discovery = PathDiscovery(
        app_json_path=None,
        consolelog_json_path=None,
        crawler_index_path=str(crawler_path),
    )
    data = discovery.data

    assert isinstance(data, dict) and data, "crawler-only discovery must not be empty"
    assert "crawler" in (discovery._data_source or "")
    pages = data.get("pages") or data.get("%p3") or {}
    assert "bTGbC" in pages
    assert pages["bTGbC"].get("elements") or pages["bTGbC"].get("%el")
    collections = data.get("api_connector_collections") or {}
    assert "col1" in collections


def test_missing_crawler_still_reports_no_source(tmp_path) -> None:
    from bubble_mcp.aria_runtime.bubble_sdk import PathDiscovery

    discovery = PathDiscovery(app_json_path=None, consolelog_json_path=None, crawler_index_path=None)
    assert discovery.data == {}
    assert discovery._data_source == "none"
