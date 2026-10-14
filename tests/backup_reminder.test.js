const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(process.env.REMINDER_SCRIPT || path.join(__dirname, "../static/backup-reminder.js"), "utf8");
const endpoint = "/settings/backups/reminder/";
const flush = () => new Promise(setImmediate);

function setup(response = { ok: true, redirected: false }) {
  const events = {};
  const button = { disabled: false };
  const error = { textContent: "" };
  const notice = { removed: false, remove() { this.removed = true; } };
  const requests = [];
  const form = {
    // HTML named controls shadow native form properties in real browsers.
    action: { toString: () => "[object HTMLInputElement]" },
    fields: { action: "snooze", csrfmiddlewaretoken: "fixture-csrf-token" },
    getAttribute: (name) => name === "action" ? endpoint : null,
    querySelector: () => button,
    addEventListener: (name, handler) => { events[name] = handler; },
  };
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
    querySelector: (selector) => selector === "[data-backup-snooze]" ? form : error,
    addEventListener: (name, handler) => { events[name] = handler; },
  };
  vm.runInNewContext(source, {
    document: { getElementById: () => dialog, querySelector: () => notice },
    FormData: class { constructor(input) { this.fields = input.fields; } },
    fetch: async (url, options) => {
      requests.push({ url: String(url), ...options });
      if (String(url) !== endpoint) return { ok: false, redirected: false };
      return typeof response === "function" ? response() : response;
    },
  });
  return { events, button, error, notice, requests, dialog };
}

test("snooze posts to the HTML action attribute despite a named action input", async () => {
  const state = setup();
  let prevented = false;
  state.events.submit({ preventDefault() { prevented = true; } });
  await flush();
  assert.equal(prevented, true);
  assert.equal(state.requests[0].url, endpoint);
  assert.equal(state.requests[0].method, "POST");
  assert.equal(state.requests[0].credentials, "same-origin");
  assert.equal(state.requests[0].body.fields.action, "snooze");
  assert.equal(state.requests[0].body.fields.csrfmiddlewaretoken, "fixture-csrf-token");
  assert.equal(state.dialog.open, false);
  assert.equal(state.notice.removed, true);
  assert.equal(state.button.disabled, false);
});

test("Escape persists the same one-day snooze before dismissing", async () => {
  const state = setup();
  state.events.cancel({ preventDefault() {} });
  await flush();
  assert.equal(state.requests[0].url, endpoint);
  assert.equal(state.dialog.open, false);
});

for (const response of [{ ok: false, redirected: false }, { ok: true, redirected: true }]) {
  test(`failed or redirected requests remain visible: ${JSON.stringify(response)}`, async () => {
    const state = setup(response);
    state.events.submit({ preventDefault() {} });
    await flush();
    assert.equal(state.dialog.open, true);
    assert.equal(state.notice.removed, false);
    assert.match(state.error.textContent, /알림을 미루지 못했어요/);
    assert.equal(state.button.disabled, false);
  });
}

test("repeated clicks send one request while the first is pending", async () => {
  let release;
  const state = setup(() => new Promise((resolve) => { release = resolve; }));
  state.events.submit({ preventDefault() {} });
  state.events.submit({ preventDefault() {} });
  assert.equal(state.requests.length, 1);
  assert.equal(state.button.disabled, true);
  release({ ok: true, redirected: false });
  await flush();
  assert.equal(state.dialog.open, false);
});
