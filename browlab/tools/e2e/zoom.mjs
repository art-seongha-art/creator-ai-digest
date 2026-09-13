// Only the photo zooms; the menu never does (while the studio is open).
// Run:  node browlab/tools/e2e/zoom.mjs
// Needs a BrowLab server with at least one 상담 시뮬레이션 design already in its gallery
// (browlab/tools/e2e/README.md says how). Settings come from the environment:
//   BROWLAB_URL    the server            (default http://127.0.0.1:8177)
//   BROWLAB_PHOTO  a face photo to upload for the suites that need one
//   BROWLAB_OUT    where screenshots go  (default ./e2e-out)
//   BROWLAB_CHROME path to a Chromium    (default: whatever Playwright finds)
import pkg from '/opt/node22/lib/node_modules/playwright/index.js';
const { chromium } = pkg;
const URL=process.env.BROWLAB_URL||'http://127.0.0.1:8177';
const SD=process.env.BROWLAB_OUT||'e2e-out';
const browser = await chromium.launch({ executablePath: process.env.BROWLAB_CHROME||undefined });
const ctx = await browser.newContext({ viewport: {width:1180,height:820}, hasTouch:true });
const page = await ctx.newPage();
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
const id = await page.evaluate(async()=>{ const g=await api('api/gallery'); return g.items.find(i=>i.kind==='design').id; });
await page.evaluate(id => openDesign(id), id);
await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
// Safari's page-zoom gesture, wherever it starts, is refused while the studio is open
const g = await page.evaluate(()=>{
  const fire=(el)=>{ const e=new Event('gesturestart',{bubbles:true,cancelable:true}); el.dispatchEvent(e); return e.defaultPrevented; };
  return {panel:fire(document.querySelector('.dz-body')), stage:fire(document.getElementById('dzStage')), tabs:fire(document.getElementById('dzTabs'))}; });
ok(g.panel && g.stage && g.tabs, `a pinch on the page is refused everywhere in the studio (panel ${g.panel}, stage ${g.stage}, tabs ${g.tabs})`);
const closed = await page.evaluate(async()=>{ await closeDz(); const e=new Event('gesturestart',{bubbles:true,cancelable:true}); document.body.dispatchEvent(e); return e.defaultPrevented; });
ok(!closed, 'outside the studio the browser keeps its own zoom (accessibility)');
await page.evaluate(id => openDesign(id), id);
await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right, null, {timeout:30000});
// ctrl+wheel over the panel does not zoom the page; the stage still zooms the picture
const w = await page.evaluate(()=>{ const e=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-100,ctrlKey:true});
  document.querySelector('.dz-body').dispatchEvent(e); return e.defaultPrevented; });
ok(w, 'ctrl+wheel (a trackpad pinch) over the panel is refused too');
const s0 = await page.evaluate(()=>DZ.view.s);
const r = await page.$eval('#dzStage', e=>{ const b=e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; });
await page.mouse.move(r.x+r.w/2, r.y+r.h/2); await page.mouse.wheel(0,-240); await page.waitForTimeout(200);
const s1 = await page.evaluate(()=>DZ.view.s);
ok(s1>s0*1.2, `the picture still zooms on the stage (${s0.toFixed(2)} → ${s1.toFixed(2)})`);
// and two fingers on the stage still pinch the picture, not the page
const p = await page.evaluate(()=>{
  const st=document.getElementById('dzStage'), r=st.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2, s0=DZ.view.s;
  const ev=(t,o)=>st.dispatchEvent(new PointerEvent(t,{bubbles:true,cancelable:true,composed:true,...o}));
  ev('pointerdown',{pointerId:31,pointerType:'touch',clientX:cx-40,clientY:cy,button:0,buttons:1,isPrimary:true});
  ev('pointerdown',{pointerId:32,pointerType:'touch',clientX:cx+40,clientY:cy,button:0,buttons:1,isPrimary:false});
  for(let k=1;k<=5;k++){ ev('pointermove',{pointerId:31,pointerType:'touch',clientX:cx-40-k*14,clientY:cy,buttons:1});
    ev('pointermove',{pointerId:32,pointerType:'touch',clientX:cx+40+k*14,clientY:cy,buttons:1}); }
  const s1=DZ.view.s; ev('pointerup',{pointerId:31,pointerType:'touch',clientX:cx-110,clientY:cy,buttons:0}); ev('pointerup',{pointerId:32,pointerType:'touch',clientX:cx+110,clientY:cy,buttons:0});
  return {s0,s1}; });
ok(p.s1>p.s0*1.3, `two fingers on the picture still pinch it (${p.s0.toFixed(2)} → ${p.s1.toFixed(2)})`);
ok(await page.$eval('.dz-body', e=>getComputedStyle(e).touchAction)==='pan-y', 'the panel scrolls but does not pinch');
console.log('errors', errs.length?errs:'none');
await browser.close();
