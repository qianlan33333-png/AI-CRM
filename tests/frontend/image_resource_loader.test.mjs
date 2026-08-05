import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const loaderSource = readFileSync(
  path.join(root, "aicrm_next/app/admin_console/static/admin_console/image_resource_loader.js"),
  "utf8",
);


function fakeResponse({ status = 200, pending = false, type = "image/png", payload = "image", retryAfter = "" } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        if (String(name).toLowerCase() === "x-aicrm-media-generation") return pending ? "pending" : "";
        if (String(name).toLowerCase() === "retry-after") return retryAfter;
        return "";
      },
    },
    body: { cancel() {} },
    async blob() { return new Blob([payload], { type }); },
  };
}


function fakeImage({ decodeFails = false } = {}) {
  const listeners = new Map();
  let source = "";
  return {
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) {
      if (listeners.get(name) === callback) listeners.delete(name);
    },
    get src() { return source; },
    set src(value) {
      source = value;
      queueMicrotask(() => {
        const callback = listeners.get(decodeFails ? "error" : "load");
        if (callback) callback();
      });
    },
  };
}


function loadHarness({ fetchImpl, timerMode = "immediate" }) {
  const delays = [];
  const pendingTimers = [];
  const revoked = [];
  let nextObjectUrl = 0;
  const context = {
    AbortController,
    Blob,
    console,
    fetch: fetchImpl,
    IntersectionObserver: undefined,
    URL: {
      createObjectURL() { nextObjectUrl += 1; return `blob:test-${nextObjectUrl}`; },
      revokeObjectURL(url) { revoked.push(url); },
    },
    setTimeout(callback, delay) {
      delays.push(delay);
      if (timerMode === "manual") {
        const timer = { callback, cleared: false };
        pendingTimers.push(timer);
        return timer;
      }
      return setTimeout(callback, 0);
    },
    clearTimeout(timer) {
      if (timer && typeof timer === "object") timer.cleared = true;
      else clearTimeout(timer);
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(loaderSource, context, { filename: "image_resource_loader.js" });
  return { api: context.ImageResourceLoader, delays, pendingTimers, revoked };
}


async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}


test("pending response is retried without stale cache and becomes ready only after decode", async () => {
  const requests = [];
  const responses = [
    fakeResponse({ pending: true, type: "image/svg+xml", retryAfter: "1" }),
    fakeResponse({ type: "image/png" }),
  ];
  const harness = loadHarness({
    async fetchImpl(_url, options) {
      requests.push(options);
      return responses.shift();
    },
  });
  const states = [];
  const image = fakeImage();
  await harness.api.loadInto(image, "/thumbnail", {
    onState(state, detail) { states.push({ state, detail }); },
  });

  assert.equal(requests.length, 2);
  assert.equal(requests[0].cache, "force-cache");
  assert.equal(requests[1].cache, "reload");
  assert.deepEqual(states.map((entry) => entry.state), ["queued", "loading", "pending", "retrying", "loading", "ready"]);
  assert.deepEqual(harness.delays, [1000]);
  assert.match(image.src, /^blob:test-/);
  assert.deepEqual(harness.revoked, [image.src]);
});


test("pending responses stop after four total requests and stay retryable", async () => {
  let requests = 0;
  const harness = loadHarness({
    async fetchImpl() {
      requests += 1;
      return fakeResponse({ pending: true, type: "image/svg+xml" });
    },
  });

  await assert.rejects(
    harness.api.loadInto(fakeImage(), "/thumbnail"),
    (error) => error.code === "image_generation_pending" && error.retryable === true,
  );
  assert.equal(requests, 4);
  assert.deepEqual(harness.delays, [1000, 2000, 4000]);
});


test("429 and 503 responses use the same bounded retry path", async () => {
  const responses = [
    fakeResponse({ status: 429, retryAfter: "1" }),
    fakeResponse({ status: 503 }),
    fakeResponse({ type: "image/jpeg" }),
  ];
  const harness = loadHarness({ async fetchImpl() { return responses.shift(); } });
  await harness.api.loadInto(fakeImage(), "/thumbnail");
  assert.deepEqual(harness.delays, [1000, 2000]);
});


test("waiting retries release both network slots for a later search result", async () => {
  const requestOrder = [];
  const controllers = [new AbortController(), new AbortController()];
  const harness = loadHarness({
    timerMode: "manual",
    async fetchImpl(url) {
      requestOrder.push(url);
      if (url === "/new-search-result") return fakeResponse({ type: "image/png" });
      return fakeResponse({ pending: true, type: "image/svg+xml" });
    },
  });

  const first = harness.api.loadInto(fakeImage(), "/old-a", { signal: controllers[0].signal });
  const second = harness.api.loadInto(fakeImage(), "/old-b", { signal: controllers[1].signal });
  const latest = harness.api.loadInto(fakeImage(), "/new-search-result");
  await flush();

  assert.deepEqual(requestOrder, ["/old-a", "/old-b", "/new-search-result"]);
  await latest;
  controllers[0].abort("search_changed");
  controllers[1].abort("search_changed");
  await assert.rejects(first, { name: "AbortError" });
  await assert.rejects(second, { name: "AbortError" });
});


test("abort during backoff stops the retry and reports aborted state", async () => {
  const controller = new AbortController();
  const states = [];
  const harness = loadHarness({
    timerMode: "manual",
    async fetchImpl() { return fakeResponse({ pending: true, type: "image/svg+xml" }); },
  });
  const promise = harness.api.loadInto(fakeImage(), "/thumbnail", {
    signal: controller.signal,
    onState(state, detail) { states.push({ state, detail }); },
  });
  await flush();
  controller.abort("search_changed");

  await assert.rejects(promise, (error) => error.name === "AbortError" && error.reason === "parent");
  assert.equal(states.at(-1).state, "aborted");
});


test("invalid responses and browser decode failures are terminal and revoke object URLs", async () => {
  const invalidHarness = loadHarness({
    async fetchImpl() { return fakeResponse({ type: "application/json" }); },
  });
  await assert.rejects(
    invalidHarness.api.loadInto(fakeImage(), "/thumbnail"),
    (error) => error.code === "image_response_invalid" && error.retryable === false,
  );

  const decodeHarness = loadHarness({
    async fetchImpl() { return fakeResponse({ type: "image/png" }); },
  });
  await assert.rejects(
    decodeHarness.api.loadInto(fakeImage({ decodeFails: true }), "/thumbnail"),
    (error) => error.code === "image_decode_failed" && error.retryable === false,
  );
  assert.deepEqual(decodeHarness.revoked, ["blob:test-1"]);
});
