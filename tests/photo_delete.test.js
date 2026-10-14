const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/photos.js"), "utf8");

function setup({ confirmed = true, ok = true, redirected = false, payload = { ok: true, count: 9 }, album = false, pending = false } = {}) {
  let submit, resolveRequest;
  const button = { disabled: false };
  const input = { disabled: true };
  const uploadButton = { disabled: true };
  const uploadStatus = { textContent: "오늘의 사진을 모두 올렸습니다." };
  const status = { textContent: "" };
  const count = { textContent: "10 / 10" };
  const csrf = { value: "fixture-csrf" };
  const figure = { removed: false, remove() { this.removed = true; } };
  const draft = { value: "아직 저장하지 않은 일기" };
  const upload = { querySelector(selector) { return { "[data-photo-input]": input, "button[type=submit]": uploadButton, "[data-photo-status]": uploadStatus }[selector]; } };
  const section = {
    dataset: {}, hasAttribute: (name) => album && name === "data-photo-album",
    querySelector(selector) { return { "[data-photo-delete-status]": status, "[data-photo-count]": count, "[data-photo-upload-form]": upload }[selector]; },
  };
  const form = {
    action: "/photos/fixture/delete/", dataset: { photoName: "테스트 사진.jpg" },
    addEventListener(event, handler) { if (event === "submit") submit = handler; },
    closest(selector) { return selector === "figure" ? figure : section; },
    querySelector(selector) { return { "button[type=submit]": button, "input[name=csrfmiddlewaretoken]": csrf }[selector]; },
  };
  const prompts = [], requests = [];
  let reloads = 0;
  class Data {
    constructor(form) { this.fields = new Map([["csrfmiddlewaretoken", form.querySelector("input[name=csrfmiddlewaretoken]").value]]); }
    set(name, value) { this.fields.set(name, value); }
  }
  const window = { confirm(message) { prompts.push(message); return confirmed; }, location: { reload() { reloads++; } } };
  vm.runInNewContext(source, {
    document: { querySelectorAll: (selector) => selector === "[data-photo-delete-form]" ? [form] : [] },
    window, FormData: Data, Uint8Array, Uint32Array, TextEncoder, Blob, URL,
    fetch(url, options) {
      requests.push({ url, options });
      const response = { ok, redirected, json: async () => payload };
      return pending ? new Promise((resolve) => { resolveRequest = () => resolve(response); }) : Promise.resolve(response);
    },
  });
  return { submit: () => submit({ preventDefault() {} }), resolve: () => resolveRequest(), form, section, prompts, requests, button, input, uploadButton, uploadStatus, status, count, figure, draft, reloads: () => reloads,
    reinitialize: () => window.initializePhotoSections({ querySelectorAll: (selector) => selector === "[data-photo-delete-form]" ? [form] : [] }) };
}

test("declining photo deletion retains the photo without sending a request", async () => {
  const state = setup({ confirmed: false });
  await state.submit();
  assert.match(state.prompts[0], /테스트 사진.jpg/);
  assert.match(state.prompts[0], /복구할 수 없습니다/);
  assert.equal(state.requests.length, 0);
  assert.equal(state.figure.removed, false);
});

test("confirmed deletion removes the card, updates capacity, and preserves unsaved writing", async () => {
  const state = setup();
  await state.submit();
  const { url, options } = state.requests[0];
  assert.equal(url, "/photos/fixture/delete/");
  assert.equal(options.method, "POST");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.body.fields.get("csrfmiddlewaretoken"), "fixture-csrf");
  assert.equal(options.body.fields.get("confirm_delete"), "yes");
  assert.equal(state.figure.removed, true);
  assert.equal(state.count.textContent, "9 / 10");
  assert.equal(state.input.disabled, false);
  assert.equal(state.uploadButton.disabled, false);
  assert.equal(state.uploadStatus.textContent, "");
  assert.equal(state.draft.value, "아직 저장하지 않은 일기");
  assert.equal(state.reloads(), 0);
  assert.equal(state.status.textContent, "사진을 삭제했습니다.");
});

for (const failure of [{ ok: false, payload: { error: "사진을 삭제하지 못했습니다." } }, { redirected: true }, { payload: {} }]) {
  test(`failed deletion retains the photo and permits retry: ${JSON.stringify(failure)}`, async () => {
    const state = setup(failure);
    await state.submit();
    assert.equal(state.figure.removed, false);
    assert.equal(state.count.textContent, "10 / 10");
    assert.equal(state.button.disabled, false);
    assert.ok(state.status.textContent);
    assert.equal(state.section.dataset.photoBusy, undefined);
  });
}

test("repeated delete submissions send only one request while deletion is pending", async () => {
  const state = setup({ pending: true });
  const first = state.submit();
  await state.submit();
  assert.equal(state.requests.length, 1);
  assert.equal(state.prompts.length, 1);
  state.resolve();
  await first;
});

test("deletion waits for a running upload without discarding the photo", async () => {
  const state = setup();
  state.section.dataset.photoBusy = "true";
  await state.submit();
  assert.equal(state.requests.length, 0);
  assert.equal(state.figure.removed, false);
  assert.match(state.status.textContent, /처리하는 중/);
});

test("album deletion refreshes the filtered count and pagination after server success", async () => {
  const state = setup({ album: true });
  await state.submit();
  assert.equal(state.reloads(), 1);
});
