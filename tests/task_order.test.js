const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup({mobile = false} = {}) {
  const events = {}, requests = [];
  let response = {}, replacement = false;
  let week;
  const makeWeek = (html = 'original week') => ({
    html, dataset: {selectedDate: '2026-10-14'},
    grid: {scrollLeft: 124, scrollWidth: 784, clientWidth: mobile ? 336 : 784, offsetLeft: 0,
      querySelector: () => ({offsetLeft: 448, clientWidth: 112})},
    dayList: {scrollTop: html === 'original week' ? 72 : 0},
    querySelector() {return this.grid;},
    querySelectorAll() {return [{dataset: {weekDate: '2026-10-14'}, querySelector: () => this.dayList}];},
    replaceWith(next) {week = next;},
  });
  week = makeWeek();
  const list = {
    dataset: {taskDate: '2026-10-14', categoryId: 'category-1', reorderUrl: '/tasks/reorder/'},
    items: [],
    querySelectorAll(selector) {
      return selector === '.is-dragging' ? this.items.filter(item => item.dragging) : [...this.items];
    },
    querySelector() { return {value: 'fixture-csrf'}; },
    append(item) { this.items = this.items.filter(existing => existing !== item); this.items.push(item); },
    insertBefore(item, before) {
      this.items = this.items.filter(existing => existing !== item);
      const index = this.items.indexOf(before);
      this.items.splice(index < 0 ? this.items.length : index, 0, item);
    },
  };
  const rows = {};
  for (const [id, status] of [['p1', 'PENDING'], ['p2', 'PENDING'], ['x', 'NOT_DONE'], ['t', 'PARTIAL'], ['d', 'DONE']]) {
    const select = {name: "status", value: status, dataset: {savedValue: status}, disabled: false,
      focus(options) { this.focusOptions = options; }};
    const retry = {hidden: !['NOT_DONE', 'PARTIAL'].includes(status)};
    const feedback = {}, button = {disabled: false};
    const row = {
      dataset: {taskId: id}, select, retry, feedback, button, offsetHeight: 20,
      getBoundingClientRect: () => ({top: 0}),
      get nextSibling() { return list.items[list.items.indexOf(this) + 1]; },
      closest(selector) { return selector === '[data-task-sort-item]' ? this : selector === '[data-task-sort-list]' ? list : null; },
      querySelector(selector) { return selector === '[data-status-select]' ? select : selector === '[data-task-retry]' ? retry : null; },
      classList: {add() {row.dragging = true;}, remove() {row.dragging = false;}},
    };
    row.form = {
      action: `/tasks/${id}/status/`,
      closest(selector) { return selector === '[data-status-form]' ? this : row.closest(selector); },
      querySelector(selector) {
        return selector === '[data-status-select]' ? select : selector === '[data-status-feedback]' ? feedback : button;
      },
    };
    row.handle = {closest(selector) {return selector === '[data-drag-handle]' ? this : row.closest(selector);}};
    rows[id] = row; list.items.push(row);
  }
  const draft = {value: '저장하지 않은 기록 🌿'};
  const document = {
    activeElement: null, documentElement: {classList: {add() {}}}, body: {dataset: {draftOwner: '7'}},
    addEventListener(name, handler) {events[name] = handler;},
    getElementById: id => id === 'task-editor-dialog' ? {querySelector: () => ({querySelector: () => null}), addEventListener() {}} : null,
    querySelector: selector => selector === '[data-week-overview]' ? week : selector === '[data-week-overview] .week-grid' ? week.grid : null,
    querySelectorAll: () => [],
  };
  vm.runInNewContext(fs.readFileSync('static/app.js', 'utf8'), {
    document, URL, Intl, Date,
    DOMParser: class {parseFromString(html) {return {querySelector: () => makeWeek(html)};}},
    FormData: class {
      constructor(form) {
        this.values = {};
        if (form) {
          const select = form.querySelector('[data-status-select]');
          if (!select.disabled) this.append(select.name, select.value);
        }
      }
      append(key, value) {(this.values[key] ||= []).push(value);}
    },
    window: {location: {href: 'https://example.test/', assign() {replacement = true;}}, addEventListener() {}, setInterval() {}},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (response.wait) return response.wait;
      return {ok: response.ok !== false, redirected: !!response.redirected, json: async () => response.body};
    },
  });
  const dataTransfer = {setData() {}};
  return {
    rows, draft, document, requests,
    get replacement() {return replacement;},
    get week() {return week;},
    ids: () => list.items.map(row => row.dataset.taskId),
    setResponse(value) {response = value;},
    save(id, status) {rows[id].select.value = status; return events.submit({target: rows[id].form, preventDefault() {}});},
    startDrag(id) {events.dragstart({target: rows[id].handle, dataTransfer});},
    dragOver(id) {
      let accepted = false;
      events.dragover({target: id ? rows[id] : {closest: selector => selector === '[data-task-sort-list]' ? list : null},
        clientY: 19, preventDefault() {accepted = true;}});
      return accepted;
    },
    endDrag() {events.dragend();},
    drop() {return events.drop({target: {closest: () => list}, preventDefault() {}});},
  };
}

test('a confirmed status change reorders existing rows without replacing the page or losing writing', async () => {
  const state = setup(), originalRows = {...state.rows};
  state.document.activeElement = state.rows.p2.select;
  state.setResponse({body: {status: 'DONE', task_ids: ['p1', 'x', 't', 'd', 'p2']}});
  await state.save('p2', 'DONE');
  assert.deepEqual(state.ids(), ['p1', 'x', 't', 'd', 'p2']);
  assert.deepEqual(state.requests[0].options.body.values.status, ['DONE']);
  assert.equal(state.draft.value, '저장하지 않은 기록 🌿');
  assert.equal(state.replacement, false);
  assert.equal(state.rows.p2.select.dataset.savedValue, 'DONE');
  assert.equal(state.rows.p2.select.disabled, false);
  assert.equal(state.rows.p2.select.focusOptions.preventScroll, true);
  for (const id of state.ids()) assert.equal(state.rows[id], originalRows[id]);
  state.setResponse({body: {status: 'PENDING', task_ids: ['p1', 'p2', 'x', 't', 'd']}});
  await state.save('p2', 'PENDING');
  assert.deepEqual(state.ids(), ['p1', 'p2', 'x', 't', 'd']);
});

test('failed status saves leave the original order, status and draft intact', async () => {
  const state = setup(), before = state.ids();
  state.setResponse({ok: false}); await state.save('p1', 'DONE');
  assert.deepEqual(state.ids(), before);
  assert.equal(state.rows.p1.select.value, 'PENDING');
  assert.equal(state.rows.p1.select.disabled, false);
  assert.equal(state.draft.value, '저장하지 않은 기록 🌿');
});

test('repeated saves wait for the pending status request', async () => {
  const state = setup(); let finish;
  state.setResponse({wait: new Promise(resolve => {finish = resolve;})});
  const pending = state.save('p1', 'DONE');
  await state.save('p1', 'DONE');
  assert.equal(state.requests.length, 1);
  finish({ok: true, json: async () => ({status: 'DONE', task_ids: ['p2', 'x', 't', 'd', 'p1']})});
  await pending;
  assert.equal(state.rows.p1.select.disabled, false);
  assert.deepEqual(state.ids(), ['p2', 'x', 't', 'd', 'p1']);
});

test('dragging changes peer order and applies the server-confirmed ordering', async () => {
  const state = setup(); state.startDrag('p1'); state.dragOver('p2');
  assert.deepEqual(state.ids(), ['p2', 'p1', 'x', 't', 'd']);
  state.setResponse({body: {ok: true, task_ids: ['p2', 'x', 't', 'd', 'p1']}});
  await state.drop();
  assert.deepEqual(state.requests[0].options.body.values.task_ids, ['p2', 'p1', 'x', 't', 'd']);
  assert.deepEqual(state.ids(), ['p2', 'x', 't', 'd', 'p1']);
});

test('dragging cannot move a task into a different saved status group', () => {
  const state = setup(), before = state.ids();
  state.startDrag('p1'); state.dragOver('d');
  assert.deepEqual(state.ids(), before);
});

for (const target of ['p1', null]) {
  test(`a browser can drop over ${target ? 'the moved row under its pointer' : 'a gap in its original list'}`, async () => {
    const state = setup();
    const expected = ['p2', 'p1', 'x', 't', 'd'];
    state.startDrag('p1');
    state.dragOver('p2');
    assert.deepEqual(state.ids(), expected);
    state.setResponse({body: {ok: true, task_ids: expected}});
    // Browsers dispatch drop only when the latest dragover was cancelled.
    if (state.dragOver(target)) await state.drop();
    state.endDrag();
    assert.deepEqual(state.ids(), expected);
    assert.equal(state.requests.length, 1);
    assert.deepEqual(state.requests[0].options.body.values.task_ids, expected);
  });
}

test('ending a drag outside its list restores the order without a save', () => {
  const state = setup(), before = state.ids();
  state.startDrag('p1'); state.dragOver('p2'); state.endDrag();
  assert.deepEqual(state.ids(), before);
  assert.equal(state.requests.length, 0);
});

for (const response of [{ok: false}, {redirected: true}, {body: {ok: false}}]) {
  test(`failed drag saves restore the original order: ${JSON.stringify(response)}`, async () => {
    const state = setup(), before = state.ids();
    state.startDrag('p1'); state.dragOver('p2'); state.setResponse(response);
    await state.drop();
    assert.deepEqual(state.ids(), before);
    assert.equal(state.draft.value, '저장하지 않은 기록 🌿');
  });
}

test('status and drag saves refresh only the week, preserving its horizontal position, focus and writing', async () => {
  const state = setup();
  const originalRows = {...state.rows};
  state.document.activeElement = state.rows.p1.select;
  state.setResponse({body: {status: 'DONE', task_ids: ['p2', 'x', 't', 'd', 'p1'], week_date: '2026-10-14', week_html: 'six remaining tasks'}});
  await state.save('p1', 'DONE');
  assert.equal(state.week.html, 'six remaining tasks');
  assert.equal(state.week.grid.scrollLeft, 124);
  assert.equal(state.week.dayList.scrollTop, 72);
  assert.equal(state.draft.value, '저장하지 않은 기록 🌿');
  assert.equal(state.replacement, false);
  assert.equal(state.rows.p1.select.focusOptions.preventScroll, true);
  for (const id of state.ids()) assert.equal(state.rows[id], originalRows[id]);
  state.setResponse({body: {status: 'PENDING', task_ids: ['p1', 'p2', 'x', 't', 'd'], week_date: '2026-10-14', week_html: 'pending restored'}});
  await state.save('p1', 'PENDING');
  assert.equal(state.week.html, 'pending restored');
  state.startDrag('p1'); state.dragOver('p2');
  state.setResponse({body: {ok: true, task_ids: ['p2', 'p1', 'x', 't', 'd'], week_date: '2026-10-14', week_html: 'reordered tasks'}});
  await state.drop();
  assert.equal(state.week.html, 'reordered tasks');
  assert.equal(state.week.grid.scrollLeft, 124);
  assert.equal(state.week.dayList.scrollTop, 72);
});

test('a late response from a previous day cannot overwrite the newly selected week', async () => {
  const state = setup(); let finish;
  state.setResponse({wait: new Promise(resolve => {finish = resolve;})});
  const pending = state.save('p1', 'DONE');
  state.week.dataset.selectedDate = '2026-10-15';
  finish({ok: true, json: async () => ({status: 'DONE', task_ids: ['p2', 'x', 't', 'd', 'p1'], week_date: '2026-10-14', week_html: 'stale week'})});
  await pending;
  assert.equal(state.week.html, 'original week');
  assert.equal(state.week.dataset.selectedDate, '2026-10-15');
});

test('a failed status save leaves the weekly overview unchanged', async () => {
  const state = setup();
  state.setResponse({ok: false, body: {week_date: '2026-10-14', week_html: 'not saved'}});
  await state.save('p1', 'DONE');
  assert.equal(state.week.html, 'original week');
});

test('a narrow weekly board initially reveals the selected date without scrolling the whole page', () => {
  const state = setup({mobile: true});
  assert.equal(state.week.grid.scrollLeft, 336);
});
