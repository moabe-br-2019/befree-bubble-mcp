"""HTTP calls of the Bubble plugin editor, made with the stored editor session.

Endpoints and payloads are the ones recorded in ``docs/capture-plugin-editor.md``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib import error, request
from urllib.parse import quote

from bubble_mcp.execution.client import (
    HttpResponse,
    HttpTransport,
    build_editor_write_headers,
    default_http_transport,
)
from bubble_mcp.sessions.store import BubbleSessionData

BUBBLE_ORIGIN = "https://bubble.io"
DEFAULT_TIMEOUT_SEC = 80.0
DEFAULT_NODE_VERSION = 22

GetTransport = Callable[[str, dict[str, str], float], HttpResponse]


class PluginEditorError(RuntimeError):
    """A plugin editor call failed (HTTP error, logged-out session or unexpected body)."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:500]


def default_get_transport(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return HttpResponse(
                status=int(response.status),
                body=response.read().decode("utf-8", errors="replace"),
                headers={str(k): str(v) for k, v in response.headers.items()},
            )
    except error.HTTPError as exc:
        return HttpResponse(
            status=int(exc.code),
            body=exc.read().decode("utf-8", errors="replace"),
            headers={str(k): str(v) for k, v in exc.headers.items()},
        )


def plugin_editor_url(plugin_id: str) -> str:
    return f"{BUBBLE_ORIGIN}/plugin_editor?id={quote(plugin_id)}"


class PluginEditorClient:
    def __init__(
        self,
        *,
        transport: HttpTransport = default_http_transport,
        get_transport: GetTransport = default_get_transport,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._transport = transport
        self._get_transport = get_transport
        self._timeout = timeout

    def _headers(self, plugin_id: str, session: BubbleSessionData) -> dict[str, str]:
        headers = build_editor_write_headers(session, {"appname": session.app_id})
        # The editor's own requests name the plugin editor page, not an app editor page.
        headers["referer"] = plugin_editor_url(plugin_id)
        headers["x-bubble-r"] = plugin_editor_url(plugin_id)
        return headers

    @staticmethod
    def _decode(response: HttpResponse, what: str) -> Any:
        if response.status in (401, 403):
            raise PluginEditorError(f"{what}: not authorized ({response.status}); run `bubble-mcp session login`.", status=response.status, body=response.body)
        if not 200 <= response.status < 300:
            raise PluginEditorError(f"{what}: HTTP {response.status}", status=response.status, body=response.body)
        text = response.body.strip()
        if text.startswith("<"):
            raise PluginEditorError(f"{what}: received HTML instead of JSON; the session has probably expired.", status=response.status, body=text)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise PluginEditorError(f"{what}: response is not JSON", status=response.status, body=text) from exc

    def _post(self, path: str, payload: dict[str, Any], plugin_id: str, session: BubbleSessionData) -> Any:
        headers = self._headers(plugin_id, session)
        response = self._transport(
            f"{BUBBLE_ORIGIN}{path}", json.dumps(payload).encode("utf-8"), headers, self._timeout
        )
        return self._decode(response, f"POST {path}")

    def get_plugin(self, plugin_id: str, session: BubbleSessionData) -> dict[str, Any]:
        headers = self._headers(plugin_id, session)
        headers.pop("content-type", None)
        response = self._get_transport(
            f"{BUBBLE_ORIGIN}/appeditor/get_plugin?id={quote(plugin_id)}", headers, self._timeout
        )
        doc = self._decode(response, "GET /appeditor/get_plugin")
        if not isinstance(doc, dict):
            raise PluginEditorError("GET /appeditor/get_plugin: expected a plugin document", body=response.body)
        return doc

    def save_plugin(self, plugin_id: str, raw: dict[str, Any], session: BubbleSessionData) -> Any:
        return self._post("/appeditor/save_plugin", {"id": plugin_id, "raw": raw}, plugin_id, session)

    def acquire_lock(self, plugin_id: str, holder: str, session: BubbleSessionData) -> bool:
        result = self._post(
            "/appeditor/plugin_editor_lock",
            {"holder": holder, "op": "acquire", "plugin_id": plugin_id},
            plugin_id,
            session,
        )
        return isinstance(result, dict) and result.get("held") is True

    def release_lock(self, plugin_id: str, holder: str, session: BubbleSessionData) -> Any:
        """Give the editor lock back so editor tabs can edit again (they show read-only while it is held)."""

        return self._post(
            "/appeditor/plugin_editor_lock",
            {"holder": holder, "op": "release", "plugin_id": plugin_id},
            plugin_id,
            session,
        )

    def mark_git_modified(self, plugin_id: str, session: BubbleSessionData) -> Any:
        return self._post(
            "/appeditor/update_plugin_meta_object",
            {"method": "update_git_modified_status", "plugin_id": plugin_id},
            plugin_id,
            session,
        )

    def build_package(
        self,
        plugin_id: str,
        package_json_string: str,
        session: BubbleSessionData,
        *,
        node_version: int = DEFAULT_NODE_VERSION,
    ) -> dict[str, Any]:
        result = self._post(
            "/appeditor/create_action_s3_package",
            {"node_version": node_version, "package_json_string": package_json_string, "plugin_id": plugin_id},
            plugin_id,
            session,
        )
        return result if isinstance(result, dict) else {"success": False, "response": result}
