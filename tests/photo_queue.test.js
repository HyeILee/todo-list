const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname,'../static/photos.js'),'utf8');

function setup({failOnce='',redirected=false,saved=0,limit=10}={}) {
  const dom=new JSDOM(`<!doctype html><textarea id="draft">아직 저장하지 않은 가상 기록</textarea><div id="day-content"><section data-photo-section data-upload-url="/upload/" data-photo-limit="${limit}"><button data-photo-choose></button><span data-photo-count>${saved} / 10</span><div data-photo-grid>${'<figure></figure>'.repeat(saved)}</div><p data-photo-delete-status></p><form action="/upload/" data-photo-upload-form><input name="csrfmiddlewaretoken" value="fixture-csrf"><input type="file" data-photo-input multiple><select data-photo-category><option value="LIFE">Life</option><option value="WORK">Work</option></select><span data-queue-count></span><div data-photo-queue></div><span data-photo-capacity></span><button type="button" data-photo-retry hidden>실패한 사진만 다시</button><button type="submit">사진 올리기</button><span data-photo-status></span></form></section></div>`,{url:'https://example.test/day/2026-11-11/',runScripts:'outside-only'});
  const {window}=dom, document=window.document, requests=[], decoded=[], canvases=[];
  window.fetch=async(url,options)=>{
    const file=options.body.get('photo');
    requests.push({url,options,name:file.name,category:options.body.get('category'),type:file.type});
    const fail=file.name===failOnce&&requests.filter(item=>item.name===failOnce).length===1;
    return {ok:!fail,redirected,json:async()=>fail?{error:'저장소 연결 실패'}:{id:String(requests.length),name:file.name,category:options.body.get('category'),category_label:options.body.get('category'),content_url:'/photo/content/',delete_url:'/photo/delete/',count:saved+document.querySelectorAll('[data-photo-grid] figure').length+1}};
  };
  window.Image=class{naturalWidth=4000;naturalHeight=3000;set src(value){decoded.push(value);queueMicrotask(()=>this.onload());}};
  window.HTMLCanvasElement.prototype.getContext=()=>({drawImage(){}});
  window.HTMLCanvasElement.prototype.toBlob=function(callback,type){canvases.push([this.width,this.height]);callback(new window.Blob(['encoded'],{type}));};
  window.confirm=()=>true; window.alert=()=>{};
  window.eval(source);
  const section=document.querySelector('[data-photo-section]'), input=document.querySelector('[data-photo-input]');
  const file=(name,size=5)=>new window.File([new Uint8Array(size)],name,{type:'image/jpeg'});
  const select=async files=>{
    Object.defineProperty(input,'files',{configurable:true,value:files});
    input.dispatchEvent(new window.Event('change',{bubbles:true}));
    await until(()=>!Array.from(document.querySelectorAll('.photo-queue-state')).some(node=>node.textContent.includes('확인하는 중')));
  };
  const submit=async()=>{document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await until(()=>!section.dataset.photoBusy);};
  return {window,document,section,input,requests,decoded,canvases,file,select,submit,close:()=>window.close()};
}
async function until(condition){for(let count=0;count<100;count++){if(condition())return;await new Promise(resolve=>setTimeout(resolve,5));}throw Error('UI did not settle');}

test('additional selections accumulate, each category is retained, and retry sends only the failed file',async()=>{
  const state=setup({failOnce:'second.jpg'});
  try{
    await state.select([state.file('first.jpg')]);
    await state.select([state.file('second.jpg'),state.file('third.jpg')]);
    assert.equal(state.document.querySelectorAll('.photo-queue-item').length,3);
    const category=state.document.querySelectorAll('.photo-queue-category')[1];
    category.value='WORK';category.dispatchEvent(new state.window.Event('change'));
    await state.submit();
    assert.deepEqual(state.requests.map(item=>item.name),['first.jpg','second.jpg','third.jpg']);
    assert.deepEqual(state.requests.map(item=>item.category),['LIFE','WORK','LIFE']);
    assert.equal(state.document.querySelectorAll('[data-photo-grid] figure').length,2);
    assert.equal(state.document.querySelectorAll('.photo-queue-item').length,1);
    assert.match(state.document.querySelector('.photo-queue-state').textContent,/저장소 연결 실패/);
    assert.equal(state.document.querySelector('[data-photo-retry]').hidden,false);
    state.document.querySelector('[data-photo-retry]').click();await until(()=>!state.section.dataset.photoBusy);
    assert.deepEqual(state.requests.map(item=>item.name),['first.jpg','second.jpg','third.jpg','second.jpg']);
    assert.equal(state.document.querySelectorAll('[data-photo-grid] figure').length,3);
    assert.equal(state.document.querySelectorAll('.photo-queue-item').length,0);
    assert.equal(state.document.querySelector('#draft').value,'아직 저장하지 않은 가상 기록');
  }finally{state.close();}
});

test('removing a queued image releases daily capacity without replacing previous selections',async()=>{
  const state=setup({saved:1,limit:3});
  try{
    await state.select([state.file('one.jpg')]);
    await state.select([state.file('two.jpg'),state.file('overflow.jpg')]);
    assert.equal(state.document.querySelectorAll('.photo-queue-item').length,2);
    assert.equal(state.input.disabled,true);
    state.document.querySelector('.photo-queue-remove').click();
    assert.equal(state.input.disabled,false);
    await state.select([state.file('replacement.jpg')]);await state.submit();
    assert.deepEqual(state.requests.map(item=>item.name),['two.jpg','replacement.jpg']);
  }finally{state.close();}
});

test('large queued phone photos use CSP-compatible data previews, compression and authenticated requests',async()=>{
  const state=setup();
  try{
    await state.select([state.file('phone.jpg',3_000_000)]);await state.submit();
    assert.match(state.decoded[0],/^data:image\/jpeg/);
    assert.deepEqual(state.canvases,[[1920,1440]]);
    assert.equal(state.requests[0].type,'image/webp');
    assert.equal(state.requests[0].options.credentials,'same-origin');
    assert.equal(state.requests[0].options.headers['X-CSRFToken'],'fixture-csrf');
  }finally{state.close();}
});

test('expired login retains the selected file for retry and a same-date task refresh preserves its DOM',async()=>{
  const state=setup({redirected:true});
  try{
    await state.select([state.file('keep.jpg')]);await state.submit();
    assert.equal(state.document.querySelectorAll('[data-photo-grid] figure').length,0);
    assert.equal(state.document.querySelectorAll('.photo-queue-item').length,1);
    const replacement=state.document.createElement('div');
    replacement.innerHTML='<section data-photo-section data-upload-url="/upload/"></section>';
    state.window.preservePhotoSelection(state.document.querySelector('#day-content'),replacement);
    assert.equal(replacement.querySelector('[data-photo-section]'),state.section);
    assert.match(replacement.querySelector('.photo-queue-name').textContent,/keep.jpg/);
  }finally{state.close();}
});
