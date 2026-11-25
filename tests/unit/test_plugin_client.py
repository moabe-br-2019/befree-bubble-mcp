from __future__ import annotations

import json
from typing import Any

import pytest

from bubble_mcp.execution.client import HttpResponse
from bubble_mcp.plugin_authoring.client import PluginEditorClient, PluginEditorError
from bubble_mcp.sessions.store import BubbleSessionData

PID = "1795610633201x773816577862139900"
SESSION = BubbleSessionData(
    app_id="mcp-test-app",
    url="https://bubble.io/page?id=mcp-test-app",
    method="POST",
    headers={"x-bubble-client-version": "abc"},
    cookies="meta_u1main=xyz",
    app_version="test",
    captured_at="2026-11-25T00:00:00Z",
    source="test",
)


class Recorder:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"method": "POST", "url": url, "payload": json.loads(body.decode()), "headers": headers})
        return HttpResponse(status=self.status, body=self._text(), headers={})

    def get(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return HttpResponse(status=self.status, body=self._text(), headers={})

    def _text(self) -> str:
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


def _client(rec: Recorder) -> PluginEditorClient:
    return PluginEditorClient(transport=rec.post, get_transport=rec.get)


def test_get_plugin_uses_get_with_session_cookie_and_plugin_referer() -> None:
    rec = Recorder(body={"human": "x", "meta_data": {}})
    assert _client(rec).get_plugin(PID, SESSION) == {"human": "x", "meta_data": {}}
    call = rec.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"https://bubble.io/appeditor/get_plugin?id={PID}"
    assert "meta_u1main=xyz" in call["headers"]["cookie"]
    assert call["headers"]["referer"] == f"https://bubble.io/plugin_editor?id={PID}"
    assert "content-type" not in call["headers"]


def test_save_lock_meta_and_package_payloads() -> None:
    rec = Recorder(body={"held": True})
    client = _client(rec)
    client.save_plugin(PID, {"human": "x"}, SESSION)
    assert client.acquire_lock(PID, "123x456", SESSION) is True
    client.mark_git_modified(PID, SESSION)
    client.build_package(PID, '{"dependencies": {}}', SESSION)
    urls = [call["url"].removeprefix("https://bubble.io") for call in rec.calls]
    assert urls == [
        "/appeditor/save_plugin",
        "/appeditor/plugin_editor_lock",
        "/appeditor/update_plugin_meta_object",
        "/appeditor/create_action_s3_package",
    ]
    assert rec.calls[0]["payload"] == {"id": PID, "raw": {"human": "x"}}
    assert rec.calls[1]["payload"] == {"holder": "123x456", "op": "acquire", "plugin_id": PID}
    assert rec.calls[2]["payload"] == {"method": "update_git_modified_status", "plugin_id": PID}
    assert rec.calls[3]["payload"] == {
        "node_version": 22,
        "package_json_string": '{"dependencies": {}}',
        "plugin_id": PID,
    }


def test_lock_not_held() -> None:
    assert _client(Recorder(body={"held": False})).acquire_lock(PID, "h", SESSION) is False


@pytest.mark.parametrize(("status", "body"), [(401, {"error": "nope"}), (200, "<html>login</html>")])
def test_errors_raise(status: int, body: Any) -> None:
    with pytest.raises(PluginEditorError):
        _client(Recorder(status=status, body=body)).get_plugin(PID, SESSION)
