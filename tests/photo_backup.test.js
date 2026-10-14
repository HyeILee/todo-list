const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/photos.js"), "utf8");

function setup({ failPhoto = false, redirected = false } = {}) {
  let click;
  const token = { value: "" };
  const controls = [{ disabled: true, checked: false, type: "checkbox" }, { disabled: true }];
  const status = { textContent: "" };
  const button = { disabled: false, addEventListener(name, handler) { click = handler; } };
  const downloads = [];
  const requests = [];
  const album = { dataset: { manifestUrl: "/photos/manifest/?scope=new" },
    querySelector(selector) { return { "[data-download-photos]": button, "[data-zip-status]": status, "[data-photo-confirm-token]": token }[selector]; },
    querySelectorAll() { return controls; } };
  const url = URL;
  url.createObjectURL = () => "blob:fixture";
  url.revokeObjectURL = () => {};
  vm.runInNewContext(source, {
    document: { querySelectorAll(selector) { return selector === "[data-photo-album]" ? [album] : []; },
      createElement() { return { click() { downloads.push(this.download); } }; } },
    window: { location: { href: "https://fixture.test/settings/backups/" } },
    fetch: async (target) => {
      requests.push(target);
      if (target.includes("manifest")) return { ok: true, redirected: false, json: async () => ({ confirmation_token: "signed-fixture", files: [{ name: "2026-10-06/1.jpg", url: "/photos/fixture/content/" }] }) };
      return { ok: !failPhoto, redirected, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer };
    }, URL: url, Blob, TextEncoder, Uint8Array, DataView, Date, setTimeout() {},
  });
  return { click: () => click(), token, controls, status, button, downloads, requests };
}

test("photo receipt becomes available only after ZIP creation and asks for explicit storage confirmation", async () => {
  const state = setup();
  assert.equal(state.token.value, "");
  await state.click();
  assert.equal(state.token.value, "signed-fixture");
  assert.equal(state.controls[0].disabled, false);
  assert.equal(state.controls[0].checked, false);
  assert.match(state.downloads[0], /photos-new-/);
  assert.match(state.status.textContent, /실제 저장된 파일/);
  assert.equal(state.requests.length, 2); // No automatic completion POST.
});

for (const failure of [{ failPhoto: true }, { redirected: true }]) {
  test(`failed or login-redirected photo download cannot be confirmed: ${JSON.stringify(failure)}`, async () => {
    const state = setup(failure);
    await state.click();
    assert.equal(state.token.value, "");
    assert.equal(state.controls[0].disabled, true);
    assert.equal(state.downloads.length, 0);
    assert.equal(state.button.disabled, false);
  });
}
