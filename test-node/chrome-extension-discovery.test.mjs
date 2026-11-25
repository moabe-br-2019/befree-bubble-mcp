import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const readExtensionFile = (name) => readFile(
  new URL(`../chrome-extension/${name}`, import.meta.url),
  "utf8",
);

const DISCOVERY_MSG = "__befree_bubble_mcp_plugin_discovery__";

async function loadContentScript(href, origFetch) {
  const source = await readExtensionFile("content.js");
  const posted = [];
  class FakeXHR {
    open() {}
    send() {}
    addEventListener() {}
  }
  const context = {
    __BEFREE_TEST__: true,
    location: new URL(href),
    document: { readyState: "complete", body: null, title: "" },
    XMLHttpRequest: FakeXHR,
    MutationObserver: undefined,
    setTimeout: () => 0,
    clearTimeout: () => {},
    URL,
    URLSearchParams,
    FormData,
    Blob,
    ArrayBuffer,
    Request,
    Response,
    console,
    fetch: origFetch || (async () => new Response("{}")),
    postMessage: (message) => posted.push(message),
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, posted, helpers: context.__befreeDiscovery };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 20));

test("discovery only on plugin_editor", async () => {
  const { helpers } = await loadContentScript("https://bubble.io/plugin_editor?id=x");
  assert.equal(helpers.isPluginEditorPage("https://bubble.io/plugin_editor?id=x"), true);
  assert.equal(helpers.isPluginEditorPage("https://bubble.io/page?id=app"), false);
  assert.equal(helpers.isPluginEditorPage("https://evil.com/plugin_editor"), false);
  assert.equal(helpers.isPluginEditorPage("https://bubble.io.evil.com/plugin_editor"), false);
});

test("discovery serializes non-JSON bodies", async () => {
  const { helpers } = await loadContentScript("https://bubble.io/plugin_editor?id=x");
  assert.deepEqual({ ...helpers.serializeBody('{"a":1}') }, { a: 1 });
  assert.equal(helpers.serializeBody("x=1"), "x=1");
  assert.deepEqual({ ...helpers.serializeBody(new URLSearchParams("a=1")) }, { a: "1" });
  assert.equal(helpers.serializeBody(new Blob(["z"])), "[binary]");
  assert.equal(helpers.serializeBody(new ArrayBuffer(2)), "[binary]");
  assert.equal(helpers.serializeBody(null), null);
});

test("discovery truncates large bodies", async () => {
  const { helpers } = await loadContentScript("https://bubble.io/plugin_editor?id=x");
  const cut = helpers.truncateBody("x".repeat(10), 4);
  assert.deepEqual({ ...cut }, { value: "xxxx", truncated: true });
  const small = helpers.truncateBody('{"a":1}');
  assert.equal(small.truncated, false);
  assert.deepEqual({ ...small.value }, { a: 1 });
});

test("discovery captures plugin editor requests", async () => {
  const { context, posted } = await loadContentScript(
    "https://bubble.io/plugin_editor?id=1700000000000x1",
    async () => new Response('{"saved":true}', { status: 200 }),
  );
  await context.fetch("https://bubble.io/appeditor/plugin_save", { method: "POST", body: '{"k":"v"}' });
  await context.fetch("https://bubble.io/package/run_debug/app.js");
  await flush();
  const discovery = posted.filter((message) => message.type === DISCOVERY_MSG);
  assert.equal(discovery.length, 1);
  const payload = discovery[0].payload;
  assert.equal(payload.pluginId, "1700000000000x1");
  assert.equal(payload.method, "POST");
  assert.equal(payload.status, 200);
  assert.equal(payload.url, "https://bubble.io/appeditor/plugin_save");
  assert.equal(payload.requestBody.k, "v");
  assert.equal(payload.responseData.saved, true);
  assert.equal(payload.responseTruncated, false);
});

test("discovery stays off in the app editor", async () => {
  const { context, posted } = await loadContentScript(
    "https://bubble.io/page?id=app",
    async () => new Response("{}", { status: 200 }),
  );
  await context.fetch("https://bubble.io/apiservice/something", { method: "POST", body: "{}" });
  await flush();
  assert.equal(posted.filter((message) => message.type === DISCOVERY_MSG).length, 0);
});

test("discovery never alters the editor request", async () => {
  const original = new Response("not json", { status: 500 });
  original.clone = () => { throw new Error("clone exploded"); };
  const { context } = await loadContentScript(
    "https://bubble.io/plugin_editor?id=x",
    async () => original,
  );
  const returned = await context.fetch("https://bubble.io/appeditor/x", { method: "POST", body: "{}" });
  assert.equal(returned, original);
});

test("bridge and background route discovery captures", async () => {
  const [bridge, background] = await Promise.all([
    readExtensionFile("bridge.js"),
    readExtensionFile("background.js"),
  ]);
  assert.match(bridge, new RegExp(DISCOVERY_MSG));
  assert.match(bridge, /BUBBLE_PLUGIN_DISCOVERY_CAPTURED/);
  assert.match(bridge, /event\.origin !== window\.location\.origin/);
  assert.match(background, /\/v1\/bubble\/plugin-editor\/discovery-ingest/);
  assert.match(background, /postLocal\(discoveryIngestUrl, payload\)/);
});

test("discovery ignores third-party hosts on the plugin editor", async () => {
  const { context, posted } = await loadContentScript(
    "https://bubble.io/plugin_editor?id=x",
    async () => new Response("{}", { status: 200 }),
  );
  await context.fetch("https://api.segment.io/v1/batch", { method: "POST", body: "{}" });
  await context.fetch("https://bubble.io.evil.com/x", { method: "POST", body: "{}" });
  await context.fetch("https://meta.bubble.io/x", { method: "POST", body: "{}" });
  await flush();
  const urls = posted.filter((message) => message.type === DISCOVERY_MSG).map((message) => message.payload.url);
  assert.deepEqual(urls, ["https://meta.bubble.io/x"]);
});

test("discovery skips streaming responses without reading them", async () => {
  const streaming = new Response("data: 1\n\n", {
    status: 200,
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
  let cloned = false;
  const originalClone = streaming.clone.bind(streaming);
  streaming.clone = () => { cloned = true; return originalClone(); };
  const { context, posted } = await loadContentScript(
    "https://bubble.io/plugin_editor?id=x",
    async () => streaming,
  );
  const returned = await context.fetch("https://bubble.io/ai/stream", { method: "POST", body: "{}" });
  await flush();
  assert.equal(returned, streaming);
  assert.equal(cloned, false);
  assert.equal(posted.filter((message) => message.type === DISCOVERY_MSG).length, 0);
});
