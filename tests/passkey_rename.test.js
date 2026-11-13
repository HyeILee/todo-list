const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {JSDOM} = require("jsdom");

const source = fs.readFileSync(path.join(__dirname, "../static/passkeys.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(fetcher) {
  const entry = id => '<li data-passkey-entry><strong data-passkey-display-name>개인 PC ' + id +
    '</strong><button type="button" data-passkey-rename-toggle aria-expanded="false" aria-controls="rename-' + id +
    '">이름 변경</button><form hidden id="rename-' + id + '" data-passkey-rename data-passkey-id="' + id +
    '"><input name="label" maxlength="60" required><button type="button" data-passkey-rename-cancel>취소</button><button type="submit">저장</button></form></li>';
  const dom = new JSDOM('<section data-passkey-page="settings" data-action="/accounts/security/action/">' +
    '<input name="csrfmiddlewaretoken" value="fixture-csrf"><input id="security-password" type="password" value="fixture-password">' +
    '<p data-passkey-error hidden></p><p data-passkey-status></p><ul>' + entry(7) + entry(8) + '</ul></section>',
    {url: "https://fixture.test/accounts/security/", runScripts: "outside-only"});
  const w = dom.window;
  Object.defineProperty(w, "isSecureContext", {value: true});
  w.PublicKeyCredential = function () {};
  Object.defineProperty(w.navigator, "credentials", {value: {}});
  const calls = [];
  w.fetch = async (url, options) => {
    calls.push({url, options, data: JSON.parse(options.body)});
    return fetcher ? fetcher(calls.at(-1)) : {
      ok: true, redirected: false, headers: {get: () => "application/json"},
      json: async () => ({ok: true, label: "개인 노트북"}),
    };
  };
  w.eval(source);
  const row = w.document.querySelector("[data-passkey-entry]");
  return {dom, w, calls, row,
    form: row.querySelector("form"), input: row.querySelector('[name="label"]'),
    toggle: row.querySelector("[data-passkey-rename-toggle]"),
    display: row.querySelector("[data-passkey-display-name]")};
}

test("editing and cancelling a device name sends no security request", () => {
  const s = setup();
  try {
    s.toggle.click();
    assert.equal(s.form.hidden, false);
    assert.equal(s.toggle.getAttribute("aria-expanded"), "true");
    assert.equal(s.w.document.activeElement, s.input);
    s.input.value = "취소할 이름";
    s.form.querySelector("[data-passkey-rename-cancel]").click();
    assert.equal(s.form.hidden, true);
    assert.equal(s.input.value, "개인 PC 7");
    assert.equal(s.w.document.activeElement, s.toggle);
    s.toggle.click();
    s.input.dispatchEvent(new s.w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    assert.equal(s.form.hidden, true);
    assert.equal(s.calls.length, 0);
  } finally { s.dom.window.close(); }
});

test("saving uses the selected key and CSRF protection, then updates its displayed name", async () => {
  const s = setup();
  try {
    s.toggle.click();
    s.input.value = "개인 노트북";
    s.form.dispatchEvent(new s.w.Event("submit", {bubbles: true, cancelable: true}));
    assert.equal(s.toggle.disabled, true);
    await tick();
    assert.equal(s.calls.length, 1);
    assert.deepEqual(s.calls[0].data, {action: "rename", passkey_id: "7", label: "개인 노트북", password: "fixture-password"});
    assert.equal(s.calls[0].options.credentials, "same-origin");
    assert.equal(s.calls[0].options.headers["X-CSRFToken"], "fixture-csrf");
    assert.equal(s.display.textContent, "개인 노트북");
    assert.equal(s.w.document.querySelectorAll("[data-passkey-display-name]")[1].textContent, "개인 PC 8");
    assert.equal(s.form.hidden, true);
    assert.equal(s.toggle.disabled, false);
    assert.equal(s.w.document.activeElement, s.toggle);
    assert.equal(s.w.document.querySelector("#security-password").value, "");
    assert.match(s.w.document.querySelector("[data-passkey-status]").textContent, /기기 이름을 변경/);
  } finally { s.dom.window.close(); }
});

test("a denied rename keeps the previous name and the editor open for correction", async () => {
  const s = setup(() => ({ok: false, redirected: false, headers: {get: () => "application/json"},
    json: async () => ({error: "먼저 등록한 패스키로 본인 확인을 해 주세요."})}));
  try {
    s.toggle.click();
    s.input.value = "새 이름";
    s.form.dispatchEvent(new s.w.Event("submit", {bubbles: true, cancelable: true}));
    await tick();
    assert.equal(s.display.textContent, "개인 PC 7");
    assert.equal(s.input.value, "새 이름");
    assert.equal(s.form.hidden, false);
    assert.equal(s.toggle.disabled, false);
    assert.equal(s.w.document.querySelector("#security-password").value, "");
    assert.equal(s.w.document.querySelector("[data-passkey-error]").hidden, false);
    assert.match(s.w.document.querySelector("[data-passkey-error]").textContent, /본인 확인/);
  } finally { s.dom.window.close(); }
});

test("a saved name is rendered as text rather than executable HTML", async () => {
  const label = '<img src=x onerror="alert(1)">';
  const s = setup(() => ({ok: true, redirected: false, headers: {get: () => "application/json"}, json: async () => ({ok: true, label})}));
  try {
    s.toggle.click();
    s.input.value = label;
    s.form.dispatchEvent(new s.w.Event("submit", {bubbles: true, cancelable: true}));
    await tick();
    assert.equal(s.display.textContent, label);
    assert.equal(s.display.querySelector("img"), null);
  } finally { s.dom.window.close(); }
});
