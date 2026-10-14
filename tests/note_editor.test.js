const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.hidden = false; }
  get textContent() { return this.children.length ? this.children.map(child => child.textContent || "").join("") : this._text || ""; }
  set textContent(value) { this.children = []; this._text = value; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, listener) { (this.listeners[key] ||= []).push(listener); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  insertBefore(node) { this.children.push(node); }
  fire(key, event = {}) { for (const listener of this.listeners[key] || []) listener(event); }
}
function setup() {
  const document = { querySelectorAll: () => [], addEventListener() {}, createElement: tag => new Element(tag), createTextNode: text => ({ tagName: "#text", textContent: text }) };
  const context = { document, window: {}, module: { exports: {} }, Event: class { constructor(type) { this.type = type; } } };
  vm.runInNewContext(fs.readFileSync("static/notes.js", "utf8"), context);
  return { ...context.module.exports, initialize: context.window.initializeNoteEditors };
}
test("bold selection toggles without changing surrounding Korean and emoji", () => {
  const { toggleBold } = setup();
  const value = "오늘 🌿 친구를 만났다";
  const start = value.indexOf("친구"), end = start + 2;
  const bold = toggleBold(value, start, end, 2000);
  assert.equal(bold.value, "오늘 🌿 **친구**를 만났다");
  const plain = toggleBold(bold.value, bold.start, bold.end, 2000);
  assert.equal(plain.value, value);
});
test("multiline selections retain blank lines and can be unbolded together", () => {
  const { toggleBold } = setup();
  const value = "  첫 줄\n\n둘째 줄  ";
  const bold = toggleBold(value, 0, value.length, 2000);
  assert.equal(bold.value, "  **첫 줄**\n\n**둘째 줄**  ");
  assert.equal(toggleBold(bold.value, bold.start, bold.end, 2000).value, value);
});
test("empty selection and formatting beyond the server limit leave text intact", () => {
  const { toggleBold } = setup();
  assert.ok(toggleBold("기록", 1, 1, 2000).error);
  assert.ok(toggleBold("a".repeat(2000), 0, 1, 2000).error);
  assert.equal(toggleBold("😀".repeat(1996), 0, 2, 2000).value.length, 3996);
});

test("B and Ctrl+B update submitted text and drafts without a preview", () => {
  const { initialize } = setup();
  const heading = new Element("div"); heading.querySelector = () => null;
  const wrapper = { querySelector: () => heading };
  const field = new Element("textarea");
  Object.assign(field, { id: "id_today_story", labels: [{textContent:"오늘 하루는"}], dataset: {maxLength:"2000"}, value:"좋은 하루", selectionStart:0, selectionEnd:2, closest:()=>wrapper, focus() {}, after(...nodes) { this.following = nodes; }, setRangeText(text,start,end) { this.value = this.value.slice(0,start)+text+this.value.slice(end); }, setSelectionRange(start,end) { this.selectionStart=start; this.selectionEnd=end; }, dispatchEvent(event) { this.fire(event.type); } });
  let inputs = 0; field.addEventListener("input", () => inputs++);
  const root = { querySelectorAll: () => [field] };
  initialize(root); initialize(root);
  assert.equal(heading.children.length, 1);
  const bold = heading.children[0].children[0];
  bold.fire("click");
  assert.equal(field.value, "**좋은** 하루");
  assert.equal(inputs, 1);
  assert.equal(heading.children[0].children.length, 4);
  assert.equal(field.following.length, 1);
  assert.equal(field.following[0].className, "note-feedback");
  assert.equal(field.following[0].hidden, true);
  field.fire("keydown", {ctrlKey:true,key:"b",preventDefault(){}});
  assert.equal(field.value, "좋은 하루");
  assert.equal(inputs, 2);
});

test("underline, size and color preserve Korean text and support changing selected styles", () => {
  const {formatSelection} = setup();
  let value = "  기억 🌿\n\n하루  ";
  const underlined = formatSelection(value, 0, value.length, "u", null, 2000);
  assert.equal(underlined.value, "  [u]기억 🌿[/u]\n\n[u]하루[/u]  ");
  assert.equal(formatSelection(underlined.value, 0, underlined.value.length, "u", null, 2000).value, value);
  const blue = formatSelection("기억", 0, 2, "color", "blue", 2000);
  const rose = formatSelection(blue.value, blue.start, blue.end, "color", "rose", 2000);
  assert.equal(rose.value, "[color=rose]기억[/color]");
  const large = formatSelection(rose.value, rose.start, rose.end, "size", "large", 2000);
  assert.equal(large.value, "[size=large][color=rose]기억[/color][/size]");
  assert.ok(formatSelection("a".repeat(2000), 0, 1, "u", null, 2000).error);
});

test("toolbar applies size, color and keyboard underline to submitted text and drafts", () => {
  const {initialize} = setup();
  const heading = new Element("div"); heading.querySelector = () => null;
  const field = new Element("textarea");
  Object.assign(field, {id:"id_memorable_sentence",labels:[{textContent:"기억 속 문장"}],dataset:{maxLength:"2000"},value:"남길 문장",selectionStart:0,selectionEnd:2,closest:()=>({querySelector:()=>heading}),focus(){},after(...nodes){this.following=nodes;},setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},dispatchEvent(event){this.fire(event.type);}});
  let drafts=0; field.addEventListener("input",()=>drafts++);
  initialize({querySelectorAll:()=>[field]});
  const tools=heading.children[0];
  assert.equal(field.value, "남길 문장");
  assert.equal(drafts, 0);
  tools.children[2].value="large"; tools.children[2].fire("change");
  tools.children[3].value="green"; tools.children[3].fire("change");
  field.fire("keydown",{ctrlKey:true,key:"u",preventDefault(){}});
  assert.equal(field.value,"[u][color=green][size=large]남길[/size][/color][/u] 문장");
  assert.equal(field.following[0].hidden,true);
  assert.equal(drafts,3);
});
