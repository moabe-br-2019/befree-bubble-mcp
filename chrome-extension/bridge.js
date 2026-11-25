/**
 * Befree Bubble MCP Companion — Bridge Script (isolated world)
 *
 * Injected at document_start in the ISOLATED world.
 * Listens for window.postMessage from the MAIN world content script,
 * then relays the payload to the background service worker via
 * chrome.runtime.sendMessage.
 *
 * This is the solution to the MAIN↔isolated-world messaging restriction:
 * only isolated-world scripts have access to chrome.runtime.
 */

(function () {
  'use strict';

  const MCP_MSG_TYPE = '__befree_bubble_mcp_crawl__';
  const MCP_WRITE_MSG_TYPE = '__befree_bubble_mcp_write__';
  const MCP_DISCOVERY_MSG_TYPE = '__befree_bubble_mcp_plugin_discovery__';
  const MESSAGE_TYPES = {
    [MCP_MSG_TYPE]: 'BUBBLE_API_CAPTURED',
    [MCP_WRITE_MSG_TYPE]: 'BUBBLE_WRITE_CAPTURED',
    [MCP_DISCOVERY_MSG_TYPE]: 'BUBBLE_PLUGIN_DISCOVERY_CAPTURED',
  };

  window.addEventListener('message', (event) => {
    // Only accept messages from our own content script (same origin, same window)
    if (event.source !== window || event.origin !== window.location.origin) return;
    if (!event.data || !Object.prototype.hasOwnProperty.call(MESSAGE_TYPES, event.data.type)) return;

    const payload = event.data.payload;
    if (!payload) return;

    const messageType = MESSAGE_TYPES[event.data.type];

    chrome.runtime.sendMessage({ type: messageType, payload }).catch(() => {
      // Background may not be ready yet; silently ignore
    });
  });
})();
