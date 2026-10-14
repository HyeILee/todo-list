const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/photos.js"), "utf8");
const settings = fs.readFileSync(path.join(__dirname, "../config/settings.py"), "utf8");
const imageSources = settings.match(/img-src ([^;]+)/)[1].split(/\s+/);

function setup({ size = 3_000_000, readError = false, corrupt = false, failUpload = false } = {}) {
  let submitHandler;
  const original = { name: "phone.jpg", type: "image/jpeg", size, lastModified: 123 };
  const input = { files: [original], value: "phone.jpg", disabled: false };
  const button = { disabled: false };
  const status = { textContent: "" };
  const count = { textContent: "0 / 10" };
  const cards = [];
  const requests = [];
  const imageURLs = [];
  const canvasSizes = [];
  const grid = { querySelectorAll: () => cards, append: (card) => cards.push(card) };
  const form = {
    dataset: {},
    action: "/photos/upload/2026-10-06/",
    addEventListener(name, handler) { if (name === "submit") submitHandler = handler; },
    querySelector(selector) {
      return {
        "[data-photo-input]": input, "[data-photo-category]": { value: "LIFE" },
        "[data-photo-status]": status, "button[type=submit]": button,
        "input[name=csrfmiddlewaretoken]": { value: "fixture-csrf" },
      }[selector];
    },
  };
  const section = { dataset: {}, querySelector(selector) {
    return { "[data-photo-upload-form]": form, "[data-photo-count]": count, "[data-photo-grid]": grid }[selector];
  } };
  class Reader {
    readAsDataURL(file) {
      queueMicrotask(() => {
        if (readError) return this.onerror();
        this.result = `data:${file.type};base64,fixture`;
        this.onload();
      });
    }
  }
  class BrowserImage {
    naturalWidth = 4000;
    naturalHeight = 3000;
    set src(url) {
      imageURLs.push(url);
      // Respect the application's actual image-source policy, including the
      // data/blob distinction that broke large photo preparation in production.
      queueMicrotask(() => {
        const permitted = imageSources.includes(`${url.split(":")[0]}:`);
        if (!permitted || corrupt) this.onerror(); else this.onload();
      });
    }
  }
  class UploadFile extends Blob {
    constructor(parts, name, options) { super(parts, options); this.name = name; this.lastModified = options.lastModified; }
  }
  class UploadData {
    fields = new Map();
    append(name, value) { this.fields.set(name, value); }
  }
  const objectURL = Object.create(URL);
  objectURL.createObjectURL = () => "blob:fixture";
  objectURL.revokeObjectURL = () => {};
  vm.runInNewContext(source, {
    document: {
      querySelectorAll: (selector) => selector === "[data-photo-section]" ? [section] : [],
      createElement(tag) {
        if (tag === "canvas") return {
          width: 0, height: 0,
          getContext() { return { drawImage() {} }; },
          toBlob(callback, type) { canvasSizes.push([this.width, this.height]); callback(new Blob(["encoded-photo"], { type })); },
        };
        return { tag, dataset: {}, children: [], listeners: {}, attributes: {},
          append(...children) { this.children.push(...children); },
          setAttribute(name, value) { this.attributes[name] = value; },
          addEventListener(name, handler) { this.listeners[name] = handler; },
        };
      },
      createTextNode: (text) => text,
    },
    window: {}, Image: BrowserImage, FileReader: Reader, File: UploadFile, FormData: UploadData,
    URL: objectURL, Blob, Uint8Array, TextEncoder, Date, queueMicrotask,
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: !failUpload, json: async () => failUpload ? { error: "사진을 저장소에 올리지 못했습니다." } : {
        name: "phone.webp", category: "LIFE", category_label: "Life", content_url: "/photos/fixture/content/", delete_url: "/photos/fixture/delete/", count: 1,
      } };
    },
  });
  return {
    submit: () => submitHandler({ preventDefault() {} }),
    original, input, button, status, count, cards, requests, imageURLs, canvasSizes,
  };
}

test("large phone photo is prepared under the real CSP and reaches authenticated upload", async () => {
  const state = setup();
  await state.submit();
  assert.equal(state.requests.length, 1);
  assert.match(state.imageURLs[0], /^data:image\/jpeg;/);
  assert.deepEqual(state.canvasSizes, [[1920, 1440]]);
  const { url, options } = state.requests[0];
  assert.equal(url, "/photos/upload/2026-10-06/");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.headers["X-CSRFToken"], "fixture-csrf");
  assert.equal(options.body.fields.get("category"), "LIFE");
  assert.equal(options.body.fields.get("photo").type, "image/webp");
  assert.equal(state.cards.length, 1);
  const deleteForm = state.cards[0].children.find((child) => child.tag === "form");
  assert.equal(deleteForm.action, "/photos/fixture/delete/");
  assert.equal(deleteForm.children[0].value, "fixture-csrf");
  assert.equal(deleteForm.children[1].textContent, "삭제");
  assert.equal(typeof deleteForm.listeners.submit, "function");
  assert.equal(state.count.textContent, "1 / 10");
  assert.match(state.status.textContent, /사진 1장을 올렸습니다/);
  assert.equal(state.input.disabled, false);
});

test("small photos retain the existing direct upload path", async () => {
  const state = setup({ size: 180_000 });
  await state.submit();
  assert.equal(state.imageURLs.length, 0);
  assert.equal(state.requests[0].options.body.fields.get("photo"), state.original);
  assert.equal(state.cards.length, 1);
});

for (const failure of [{ readError: true }, { corrupt: true }]) {
  test(`unreadable photo makes no upload request and permits retry: ${JSON.stringify(failure)}`, async () => {
    const state = setup(failure);
    await state.submit();
    assert.equal(state.requests.length, 0);
    assert.match(state.status.textContent, /읽을 수 없습니다/);
    assert.equal(state.input.disabled, false);
    assert.equal(state.button.disabled, false);
  });
}

test("storage failure is shown without displaying an uploaded photo", async () => {
  const state = setup({ failUpload: true });
  await state.submit();
  assert.equal(state.requests.length, 1);
  assert.equal(state.cards.length, 0);
  assert.match(state.status.textContent, /저장소에 올리지 못했습니다/);
  assert.equal(state.button.disabled, false);
});
