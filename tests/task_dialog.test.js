const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function setup(initial = {}, submitResult = "invalid") {
  const documentEvents = {}, dialogEvents = {}, confirmations = [], requests = [];
  let acceptDiscard = false, focused = 0, navigated = null, dayReplaced = false;
  const createForm = (values) => ({
    action: "/tasks/add/?fragment=1", method: "post",
    elements: Object.entries({title: "", category: "1", task_date: "2026-10-08", details: "", is_important: false, ...values})
      .map(([name, value]) => ({name, type: name === "is_important" ? "checkbox" : "text", value, checked: value, focus() {}})),
    closest(selector) { return selector === "[data-task-dialog-form]" ? this : null; },
  });
  let form, statusResponse;
  const content = {
    set innerHTML(result) {
      form = result === "conflict" ? null : createForm(form ? Object.fromEntries(form.elements.map(field =>
        [field.name, field.type === "checkbox" ? field.checked : field.value])) : initial);
    },
    querySelector(selector) { return selector === "[data-task-dialog-form]" ? form : form?.elements[0]; },
    querySelectorAll() { return []; },
  };
  const dialog = {
    open: false,
    querySelector() { return content; },
    addEventListener(name, handler) { dialogEvents[name] = handler; },
    showModal() { this.open = true; },
    close() { this.open = false; dialogEvents.close?.(); },
    closest() { return null; },
  };
  const day = {querySelectorAll: () => [], querySelector: () => null, replaceWith() { dayReplaced = true; }};
  const document = {
    documentElement: {classList: {add() {}}}, body: {dataset: {draftOwner: "7"}},
    getElementById: id => id === "task-editor-dialog" ? dialog : null,
    querySelector: selector => selector === "#day-content" ? day : null,
    querySelectorAll: () => [],
    addEventListener(name, handler) { documentEvents[name] = handler; },
  };
  vm.runInNewContext(fs.readFileSync("static/app.js", "utf8"), {
    document, URL, Intl, Date, FormData: class {},
    DOMParser: class {parseFromString() { return {querySelector: () => day}; }},
    window: {
      location: {href: "https://example.test/", assign(url) { navigated = url; }},
      history: {pushState() {}}, addEventListener() {}, setInterval() {},
      confirm(message) { confirmations.push(message); return acceptDiscard; },
    },
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url === "/tasks/1/status/") return {ok: !statusResponse.fail, redirected: false,
        json: async () => ({status: statusResponse.status})};
      const submitted = options.method === "POST";
      return {ok: !submitted || submitResult !== "conflict", status: submitted && submitResult === "conflict" ? 409 : 200,
        redirected: submitted && submitResult === "saved", url: "https://example.test/",
        text: async () => submitted ? submitResult : "initial"};
    },
  });
  const click = async target => documentEvents.click({target, preventDefault() {}});
  return {
    dialog, confirmations, requests,
    get navigated() { return navigated; }, get dayReplaced() { return dayReplaced; },
    get focused() { return focused; }, get form() { return form; },
    async open() {
      const trigger = {href: "https://example.test/tasks/add/", focus() { focused++; },
        closest: selector => selector === "[data-task-dialog]" ? trigger : null};
      await click(trigger);
    },
    field(name) { return form.elements.find(field => field.name === name); },
    allowDiscard(value) { acceptDiscard = value; },
    async backdrop() { dialogEvents.click?.({target: dialog}); await click(dialog); },
    async inside() { await click({closest: () => null}); },
    async closeButton(label) { await click({textContent: label, closest: selector => selector === "[data-dialog-close]" ? {} : null}); },
    escape() {
      const event = {defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
      dialogEvents.cancel?.(event);
      if (!event.defaultPrevented) dialog.close();
    },
    async save() { await documentEvents.submit({target: form, preventDefault() {}}); },
    async saveStatus(status, previous = "PENDING", fail = false) {
      statusResponse = {status, fail};
      const retryLink = {hidden: !["NOT_DONE", "PARTIAL"].includes(previous)};
      const select = {value: status, dataset: {savedValue: previous}};
      const feedback = {textContent: ""};
      const statusForm = {
        action: "/tasks/1/status/",
        closest(selector) {
          if (selector === "[data-status-form]") return this;
          if (selector === "[data-task-sort-item]") return {querySelector: () => retryLink};
          return null;
        },
        querySelector(selector) {
          if (selector === "[data-status-select]") return select;
          if (selector === "[data-status-feedback]") return feedback;
          return null;
        },
      };
      await documentEvents.submit({target: statusForm, preventDefault() {}});
      return {retryLink, select, feedback};
    },
  };
}

test("clicking outside or inside the editor retains all unsaved task fields", async () => {
  const state = setup(); await state.open();
  const draft = {title: "회의 준비 🌿", category: "2", task_date: "2026-10-09", details: "첫 줄\n둘째 줄"};
  for (const [name, value] of Object.entries(draft)) state.field(name).value = value;
  state.field("is_important").checked = true;
  await state.backdrop(); await state.inside();
  assert.equal(state.dialog.open, true);
  for (const [name, value] of Object.entries(draft)) assert.equal(state.field(name).value, value);
  assert.equal(state.field("is_important").checked, true);
  assert.equal(state.confirmations.length, 0);
});

test("Escape keeps changed text unless the user confirms discarding it", async () => {
  const state = setup(); await state.open(); state.field("title").value = "진행 중";
  state.escape();
  assert.equal(state.dialog.open, true);
  assert.equal(state.field("title").value, "진행 중");
  assert.match(state.confirmations[0], /저장하지 않고 닫을까요/);
  state.allowDiscard(true); state.escape();
  assert.equal(state.dialog.open, false);
  assert.equal(state.focused, 1);
});

for (const label of ["취소", "×"]) {
  test(`${label} retains the draft when discard is declined and closes when confirmed`, async () => {
    const state = setup(); await state.open(); state.field("details").value = "작성한 메모";
    await state.closeButton(label);
    assert.equal(state.dialog.open, true);
    assert.equal(state.field("details").value, "작성한 메모");
    state.allowDiscard(true); await state.closeButton(label);
    assert.equal(state.dialog.open, false);
  });
}

test("unchanged prefilled tasks close directly, including after a change is reverted", async () => {
  const state = setup({title: "다시 진행", is_important: true}); await state.open();
  state.field("title").value = "변경"; state.field("title").value = "다시 진행";
  state.escape();
  assert.equal(state.dialog.open, false);
  assert.equal(state.confirmations.length, 0);
});

test("changing only importance also requires confirmation before closing", async () => {
  const state = setup({title: "기존 일정"}); await state.open();
  state.field("is_important").checked = true; state.escape();
  assert.equal(state.dialog.open, true);
  assert.equal(state.confirmations.length, 1);
});

test("validation errors do not mark submitted draft values as saved", async () => {
  const state = setup(); await state.open(); state.field("title").value = "미완성 일정";
  await state.save(); state.escape();
  assert.equal(state.dialog.open, true);
  assert.equal(state.field("title").value, "미완성 일정");
  assert.equal(state.confirmations.length, 1);
});

test("conflict responses retain the discard guard for the submitted draft", async () => {
  const state = setup({title: "기존 일정"}, "conflict"); await state.open();
  state.field("title").value = "수정한 일정"; await state.save(); state.escape();
  assert.equal(state.dialog.open, true);
  assert.equal(state.confirmations.length, 1);
});

test("successful saves close the editor without a discard prompt", async () => {
  const state = setup({}, "saved"); await state.open(); state.field("title").value = "저장할 일정";
  await state.save();
  assert.equal(state.dialog.open, false);
  assert.equal(state.confirmations.length, 0);
  assert.equal(state.dayReplaced, true);
  assert.equal(state.navigated, null);
});


test("saving X or partial status shows retry immediately; pending and done hide it", async () => {
  const state = setup();
  for (const status of ["NOT_DONE", "PARTIAL", "DONE", "PENDING"]) {
    const result = await state.saveStatus(status, "DONE");
    assert.equal(result.retryLink.hidden, status === "DONE" || status === "PENDING");
    assert.equal(result.select.dataset.savedValue, status);
    assert.equal(result.feedback.textContent, "저장됨");
  }
});

test("failed status saves preserve the previous status and retry visibility", async () => {
  const state = setup();
  for (const [previous, attempted] of [["PENDING", "PARTIAL"], ["PARTIAL", "DONE"]]) {
    const result = await state.saveStatus(attempted, previous, true);
    assert.equal(result.select.value, previous);
    assert.equal(result.retryLink.hidden, previous === "PENDING");
    assert.equal(result.feedback.textContent, "저장 실패. 다시 시도하세요.");
  }
});
