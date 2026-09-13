// Pen and palm on an iPad: a stroke must survive a palm landing, two fingers must still pinch.
// Run:  node browlab/tools/e2e/pen.mjs
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
const ctx = await browser.newContext({ viewport: {width:1180,height:820}, hasTouch:true, isMobile:false });
const page = await ctx.newPage();
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
const id = await page.evaluate(async()=>{ const g=await api('api/gallery'); return g.items.find(i=>i.kind==='design').id; });
await page.evaluate(id => openDesign(id), id);
await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
await page.evaluate(()=>zoomBrows());
// synth pen + palm: dispatch real PointerEvents with pointerType 'pen' / 'touch'
const res = await page.evaluate(()=>{
  const st=document.getElementById('dzStage'), r=st.getBoundingClientRect(), {s,tx,ty}=DZ.view;
  const h=handlePoints('right').find(x=>x.k==='aT');
  const X=r.left+h.x*s+tx, Y=r.top+h.y*s+ty;
  const before={...DZ.brows.right.aT};
  const ev=(type,opts)=>st.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,composed:true,...opts}));
  ev('pointerdown',{pointerId:1,pointerType:'pen',clientX:X,clientY:Y,button:0,buttons:1,isPrimary:true});
  const started=!!DZ.brows.right;
  ev('pointermove',{pointerId:1,pointerType:'pen',clientX:X,clientY:Y-10,buttons:1});
  // the palm lands
  ev('pointerdown',{pointerId:2,pointerType:'touch',clientX:X+220,clientY:Y+160,button:0,buttons:1,isPrimary:false});
  ev('pointermove',{pointerId:2,pointerType:'touch',clientX:X+240,clientY:Y+170,buttons:1});
  const viewMid={...DZ.view};
  ev('pointermove',{pointerId:1,pointerType:'pen',clientX:X,clientY:Y-30,buttons:1});
  const afterDrag={...DZ.brows.right.aT};
  ev('pointerup',{pointerId:2,pointerType:'touch',clientX:X+240,clientY:Y+170,buttons:0});
  const afterPalmUp={...DZ.brows.right.aT};
  ev('pointermove',{pointerId:1,pointerType:'pen',clientX:X,clientY:Y-45,buttons:1});
  const afterMore={...DZ.brows.right.aT};
  ev('pointerup',{pointerId:1,pointerType:'pen',clientX:X,clientY:Y-45,buttons:0});
  return {before, afterDrag, afterPalmUp, afterMore, movedPx:(before.y-afterMore.y)*s, scaleUnchanged:Math.abs(viewMid.s-DZ.view.s)<1e-9, hist:DZ.hist.length};
});
ok(res.movedPx>40 && res.scaleUnchanged, `pen keeps dragging while a palm touches down (moved ${res.movedPx.toFixed(0)} px on screen, view unchanged: ${res.scaleUnchanged})`);
ok(res.afterPalmUp.y===res.afterDrag.y, 'the palm lifting does not end the stroke');
ok(res.afterMore.y<res.afterPalmUp.y, 'the pen keeps moving the handle after the palm lifts');
ok(res.hist>=2, 'the finished pen stroke is one undo step');
// two real fingers still pinch
const pinch = await page.evaluate(()=>{
  const st=document.getElementById('dzStage'), r=st.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2, s0=DZ.view.s;
  const ev=(type,opts)=>st.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,composed:true,...opts}));
  ev('pointerdown',{pointerId:11,pointerType:'touch',clientX:cx-40,clientY:cy,button:0,buttons:1,isPrimary:true});
  ev('pointerdown',{pointerId:12,pointerType:'touch',clientX:cx+40,clientY:cy,button:0,buttons:1,isPrimary:false});
  for(let k=1;k<=5;k++){ ev('pointermove',{pointerId:11,pointerType:'touch',clientX:cx-40-k*15,clientY:cy,buttons:1});
    ev('pointermove',{pointerId:12,pointerType:'touch',clientX:cx+40+k*15,clientY:cy,buttons:1}); }
  const s1=DZ.view.s; ev('pointerup',{pointerId:11,pointerType:'touch',clientX:cx-115,clientY:cy,buttons:0}); ev('pointerup',{pointerId:12,pointerType:'touch',clientX:cx+115,clientY:cy,buttons:0});
  return {s0,s1};
});
ok(pinch.s1>pinch.s0*1.4, `two fingers still pinch (${pinch.s0.toFixed(2)} → ${pinch.s1.toFixed(2)})`);
// a single finger still drags a handle
const finger = await page.evaluate(()=>{
  const st=document.getElementById('dzStage'), r=st.getBoundingClientRect(), {s,tx,ty}=DZ.view;
  const h=handlePoints('left').find(x=>x.k==='t'); const X=r.left+h.x*s+tx, Y=r.top+h.y*s+ty, b0={...DZ.brows.left.t};
  const ev=(type,opts)=>st.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,composed:true,...opts}));
  ev('pointerdown',{pointerId:21,pointerType:'touch',clientX:X,clientY:Y,button:0,buttons:1,isPrimary:true});
  ev('pointermove',{pointerId:21,pointerType:'touch',clientX:X+20,clientY:Y-18,buttons:1});
  ev('pointerup',{pointerId:21,pointerType:'touch',clientX:X+20,clientY:Y-18,buttons:0});
  return {moved:Math.hypot(DZ.brows.left.t.x-b0.x, DZ.brows.left.t.y-b0.y)*DZ.view.s};
});
ok(finger.moved>15, `one finger still drags a handle (${finger.moved.toFixed(0)} px)`);
console.log('errors', errs.length?errs:'none');
await browser.close();
