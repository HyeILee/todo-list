const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/session-security.js"), "utf8");
const flush = () => new Promise(setImmediate);

function setup(response = { status: 200, ok: true }) {
  let now = 1_000_000;
  let submissions = 0;
  let timer;
  let channel;
  const events = {};
  const requests = [];
  const toast = { hidden: true, textContent: "" };
  const form = { querySelector: () => ({ value: "fixture-csrf" }), requestSubmit: () => { submissions++; } };
  vm.runInNewContext(source, {
    Date: { now: () => now }, Number,
    document: {
      currentScript: { dataset: { idleSeconds: "1800", activityUrl: "/accounts/activity/" } },
      body: { dataset: { draftOwner: "7" } }, hidden: false,
      querySelector: () => form, getElementById: () => toast,
      addEventListener: (name, handler) => { events[name] = handler; },
    },
    window: { addEventListener: (name, handler) => { events[name] = handler; } },
    BroadcastChannel: class {
      constructor() { channel = this; }
      postMessage() {}
    },
    setInterval: (handler) => { timer = handler; },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (response instanceof Error) throw response;
      return response;
    },
  });
  return {
    events, requests, toast,
    advance: (ms) => { now += ms; }, tick: () => timer(),
    activity: () => events.input({ isTrusted: true }),
    sharedActivity: () => channel.onmessage({ data: { type: "activity", at: now } }),
    get submissions() { return submissions; },
  };
}

test("idle logout fires once after 30 minutes, including a suspended tab returning", () => {
  const state = setup();
  state.advance(29 * 60 * 1000);
  state.tick();
  assert.equal(state.submissions, 0);
  state.advance(60 * 1000);
  state.events.focus();
  state.tick();
  assert.equal(state.submissions, 1);
});

test("typing keeps the session alive with a throttled CSRF-protected request", async () => {
  const state = setup();
  for (let minute = 0; minute < 40; minute++) {
    state.advance(60 * 1000);
    state.activity();
    state.tick();
    await flush();
  }
  assert.equal(state.submissions, 0);
  assert.equal(state.requests.length, 8);
  assert.equal(state.requests[0].url, "/accounts/activity/");
  assert.equal(state.requests[0].method, "POST");
  assert.equal(state.requests[0].credentials, "same-origin");
  assert.equal(state.requests[0].headers["X-CSRFToken"], "fixture-csrf");
});

test("activity in another tab prevents an idle tab from logging out the shared session", () => {
  const state = setup();
  state.advance(29 * 60 * 1000);
  state.sharedActivity();
  state.advance(2 * 60 * 1000);
  state.tick();
  assert.equal(state.submissions, 0);
});

test("synthetic events do not extend the idle deadline", () => {
  const state = setup();
  state.advance(29 * 60 * 1000);
  state.events.input({ isTrusted: false });
  state.advance(60 * 1000);
  state.tick();
  assert.equal(state.submissions, 1);
});

test("expired authentication warns an active writer without discarding their draft", async () => {
  const state = setup({ status: 401, ok: false });
  state.advance(5 * 60 * 1000);
  state.activity();
  await flush();
  assert.equal(state.submissions, 0);
  assert.equal(state.toast.hidden, false);
  assert.match(state.toast.textContent, /작성 중인 내용을 복사/);
  for (let minute = 0; minute < 40; minute++) {
    state.advance(60 * 1000);
    state.activity();
    state.tick();
  }
  assert.equal(state.submissions, 0);
  assert.equal(state.requests.length, 1);
});

test("connection failure retains writing and backs off instead of retrying on every keystroke", async () => {
  const state = setup(new Error("offline"));
  state.advance(5 * 60 * 1000);
  state.activity();
  await flush();
  for (let key = 0; key < 10; key++) state.activity();
  assert.equal(state.requests.length, 1);
  assert.equal(state.submissions, 0);
  state.advance(60 * 1000);
  state.activity();
  await flush();
  assert.equal(state.requests.length, 2);
});
