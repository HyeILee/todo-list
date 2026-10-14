import {spawn} from 'node:child_process';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve, sep, basename} from 'node:path';
import assert from 'node:assert/strict';

const base = 'http://127.0.0.1:8001';
const profile = await mkdtemp(join(tmpdir(), 'dugyeon-edge-'));
const edge = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-port=9223', `--user-data-dir=${profile}`, 'about:blank'],
  {windowsHide: true, stdio: 'ignore'});
let ws;
try {
  let target;
  for (let i = 0; i < 100; i++) {
    try { target = (await (await fetch('http://127.0.0.1:9223/json/list')).json()).find(t => t.type === 'page'); if (target) break; } catch {}
    await new Promise(r => setTimeout(r, 100));
  }
  assert(target, 'Edge debugger ready');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  const loads = new Set();
  let lastStatus;
  ws.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) callback.reject(new Error(JSON.stringify(message.error)));
      else callback.resolve(message.result);
    }
    if (message.method === 'Page.loadEventFired') for (const resolve of [...loads]) resolve();
    if (message.method === 'Network.responseReceived' && message.params.type === 'Document') lastStatus = message.params.response.status;
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId; pending.set(id, {resolve, reject}); ws.send(JSON.stringify({id, method, params}));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    assert(!result.exceptionDetails, 'Browser JavaScript evaluation succeeds');
    return result.result.value;
  };
  const load = async action => {
    let handler;
    const loaded = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { loads.delete(handler); reject(new Error('Page load timeout')); }, 15000);
      handler = () => { clearTimeout(timer); loads.delete(handler); resolve(); };
      loads.add(handler);
    });
    await action(); await loaded;
    assert.equal(lastStatus, 200);
    assert.equal(await evaluate("document.body.innerText.includes('AttributeError')"), false);
  };
  const navigate = path => load(() => send('Page.navigate', {url: base + path}));
  await send('Page.enable'); await send('Network.enable');
  await navigate('/accounts/login/');
  await load(() => evaluate(`document.querySelector('[name=username]').value='browser-audit'; document.querySelector('[name=password]').value=${JSON.stringify(process.env.BROWSER_AUDIT_PASSWORD)}; document.querySelector('.login-form button[type=submit]').click()`));
  assert.equal(await evaluate('location.pathname'), '/');
  assert.equal(await evaluate("document.querySelectorAll('[data-task-sort-list]').length"), 3);
  console.log('PASS browser login and dashboard: 3 populated categories');
  await navigate('/tasks/all/');
  const ids = await evaluate("Array.from(document.querySelectorAll('input[name=categories]'), e=>e.value)");
  assert.equal(ids.length, 4);
  for (const id of ids) {
    await navigate('/tasks/all/');
    await load(() => evaluate(`document.querySelector('input[name=categories][value="${id}"]').click(); document.querySelector('[name=date_mode]').value='all'; document.querySelector('.filter-panel button[type=submit]').click()`));
    assert.equal(await evaluate("new URL(location.href).searchParams.get('categories')"), id);
    console.log(`PASS browser category search ${id}: HTTP 200`);
    await navigate('/categories/');
    await load(() => evaluate(`document.querySelector('a[href="?edit=${id}"]').click()`));
    console.log(`PASS browser category edit ${id}: HTTP 200`);
  }
  for (const path of ['/', '/records/', '/inbox/', '/settings/', '/tasks/new/']) {
    await navigate(path); console.log(`PASS browser ${path}: HTTP 200`);
  }
  await navigate('/');
  const editLinks = await evaluate("Array.from(document.querySelectorAll('a[data-task-dialog]'), e=>e.getAttribute('href')).filter(p=>p.includes('/edit/'))");
  for (const link of editLinks) {
    await evaluate(`document.querySelector('a[href="${link}"]').click()`);
    let visible = false;
    for (let i = 0; i < 100; i++) {
      visible = await evaluate("document.querySelector('dialog').open && !!document.querySelector('dialog #id_title')");
      if (visible) break;
      await new Promise(r=>setTimeout(r, 50));
    }
    assert(visible, 'Task dialog rendered');
    await evaluate("document.querySelector('[data-dialog-close]').click()");
    console.log(`PASS browser task dialog ${link}`);
  }
  await send('Browser.close');
} finally {
  if (ws) ws.close();
  if (edge.exitCode === null) {
    await Promise.race([new Promise(r=>edge.once('exit', r)), new Promise(r=>setTimeout(r, 2000))]);
    if (edge.exitCode === null) edge.kill();
  }
  const resolvedProfile = resolve(profile);
  assert(resolvedProfile.startsWith(resolve(tmpdir()) + sep) && basename(resolvedProfile).startsWith('dugyeon-edge-'));
  await rm(resolvedProfile, {recursive: true, force: true, maxRetries: 5, retryDelay: 200});
}
