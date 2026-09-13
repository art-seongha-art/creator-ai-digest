// 잔털 지우기: it clears hair where the brush passes, keeps the skin grain, and undoes as one stroke.
// Run:  node browlab/tools/e2e/heal.mjs
// Needs a BrowLab server with at least one 상담 시뮬레이션 design already in its gallery
// (browlab/tools/e2e/README.md says how). Settings come from the environment:
//   BROWLAB_URL    the server            (default http://127.0.0.1:8177)
//   BROWLAB_PHOTO  a face photo to upload for the suites that need one
//   BROWLAB_OUT    where screenshots go  (default ./e2e-out)
//   BROWLAB_CHROME path to a Chromium    (default: whatever Playwright finds)
import pkg from '/opt/node22/lib/node_modules/playwright/index.js';
import fs from 'fs';
const { chromium } = pkg;
const URL=process.env.BROWLAB_URL||'http://127.0.0.1:8177';
const SD=process.env.BROWLAB_OUT||'e2e-out';
const browser = await chromium.launch({ executablePath: process.env.BROWLAB_CHROME||undefined });
const page = await browser.newPage({ viewport: {width:1180,height:820} });
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
const id = await page.evaluate(async()=>{ const g=await api('api/gallery'); return g.items.find(i=>i.kind==='design').id; });
await page.evaluate(id => openDesign(id), id);
await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
await page.evaluate(()=>{ DZ.brows=initBrows(); DZ.retouch=[]; DZ.base=null; DZ.healed=null; DZ.hist=[]; DZ.histAt=-1; pushHist(); zoomBrows(); dzRender(); });
// metrics helper in the page
await page.evaluate(()=>{ window.__m=(r)=>{ const c=document.createElement('canvas'); c.width=r.w; c.height=r.h;
  c.getContext('2d').drawImage(DZ.base||DZ.photo,r.x,r.y,r.w,r.h,0,0,r.w,r.h);
  const d=c.getContext('2d').getImageData(0,0,r.w,r.h).data,w=r.w,h=r.h,n=w*h;
  const L=new Float32Array(n); for(let i=0;i<n;i++) L[i]=0.299*d[i*4]+0.587*d[i*4+1]+0.114*d[i*4+2];
  const t=new Float32Array(n),B=new Float32Array(n),rb=7;
  for(let y=0;y<h;y++) for(let x=0;x<w;x++){ let s=0,c2=0; for(let k=-rb;k<=rb;k++){ const xx=x+k; if(xx>=0&&xx<w){s+=L[y*w+xx];c2++;} } t[y*w+x]=s/c2; }
  for(let y=0;y<h;y++) for(let x=0;x<w;x++){ let s=0,c2=0; for(let k=-rb;k<=rb;k++){ const yy=y+k; if(yy>=0&&yy<h){s+=t[yy*w+x];c2++;} } B[y*w+x]=s/c2; }
  let dark=0,hp=0; for(let i=0;i<n;i++){ const e=L[i]-B[i]; hp+=e*e; if(e<-14) dark++; }
  return {dark:+(dark/n*100).toFixed(2), grain:+Math.sqrt(hp/n).toFixed(2)}; }; });
const band = await page.evaluate(()=>{ const b=DZ.brows.right, ipd=DZ.ipdPx, top=Math.min(b.hT.y,b.mT.y,b.aT.y);
  return {x:Math.round(b.aT.x-80), y:Math.round(top-0.115*ipd), w:160, h:Math.round(0.10*ipd)}; });
const m0 = await page.evaluate(b=>__m(b), band);
// paint it with the pointer, as a person would: one pass left to right
await page.click('#dzHeal');
const path = await page.evaluate(b=>{ const {s,tx,ty}=DZ.view, r=document.getElementById('dzStage').getBoundingClientRect();
  const y=b.y+b.h/2; return {x0:r.left+b.x*s+tx, x1:r.left+(b.x+b.w)*s+tx, y:r.top+y*s+ty}; }, band);
const t0=Date.now();
await page.mouse.move(path.x0, path.y); await page.mouse.down();
const mid = await page.evaluate(()=>DZ.retouch.length===0 && !!DZ.base);
await page.mouse.move(path.x1, path.y, {steps:40});
const painting = await page.evaluate(b=>({stamps:DZ.stroke?DZ.stroke.pts.length:0, m:__m(b)}), band);
await page.mouse.up();
const ms=Date.now()-t0;
const m1 = await page.evaluate(b=>__m(b), band);
ok(mid, 'the very first touch already heals (no waiting for the finger to lift)');
ok(painting.m.dark < m0.dark*0.85, `it clears as the finger moves — mid-stroke dark ${m0.dark}% → ${painting.m.dark}%`);
// the lane the brush actually swept (its own width), not the whole band: one pass cannot
// cover a band taller than the brush, and pretending otherwise measures the test, not the tool
const lane = await page.evaluate(b=>{ const r=DZ.healMm*pxPerMm();
  return {x:b.x, y:Math.round(b.y+b.h/2-r*0.8), w:b.w, h:Math.max(4,Math.round(r*1.6))}; }, band);
const lane1 = await page.evaluate(l=>__m(l), lane);
ok(lane1.dark < m0.dark*0.3, `the swept lane is clear after one pass (${m0.dark}% → ${lane1.dark}%)`);
ok(m1.grain > m0.grain*0.55, `skin texture survives (grain ${m0.grain} → ${m1.grain})`);
console.log('  stroke of', painting.stamps, 'dabs drawn in', ms, 'ms');
ok(ms < 3000, 'a full stroke keeps up with the hand');
// undo, redo, and replay from the saved state must give the same pixels
const after = await page.evaluate(b=>{ const c=document.createElement('canvas'); c.width=b.w; c.height=b.h;
  c.getContext('2d').drawImage(DZ.base,b.x,b.y,b.w,b.h,0,0,b.w,b.h); return c.toDataURL(); }, band);
await page.keyboard.press('Control+z'); await page.waitForTimeout(200);
ok(await page.evaluate(()=>DZ.retouch.length===0 && !DZ.base), 'undo takes the whole stroke back at once');
await page.click('#dzRedo'); await page.waitForTimeout(300);
const redo = await page.evaluate(b=>{ const c=document.createElement('canvas'); c.width=b.w; c.height=b.h;
  c.getContext('2d').drawImage(DZ.base,b.x,b.y,b.w,b.h,0,0,b.w,b.h); return c.toDataURL(); }, band);
ok(redo===after, 'redo reproduces exactly the same skin, dab for dab');
// a second pass along the rest of the band finishes it
for(const dy of [-0.3, 0.3]){
  const yy = await page.evaluate(([b,dy])=>{ const {s,ty}=DZ.view, r=document.getElementById('dzStage').getBoundingClientRect();
    return r.top+(b.y+b.h*(0.5+dy))*s+ty; }, [band, dy]);
  await page.mouse.move(path.x0, yy); await page.mouse.down(); await page.mouse.move(path.x1, yy, {steps:40}); await page.mouse.up();
}
const m2 = await page.evaluate(b=>__m(b), band);
ok(m2.dark < m0.dark*0.35, `three passes clear the whole band (${m0.dark}% → ${m2.dark}%)`);
ok(m2.grain > m0.grain*0.5, `and the skin still has grain (${m0.grain} → ${m2.grain})`);

// a wider look: the whole brow with the band above it, original over healed
await page.evaluate(()=>{ const b=DZ.brows.right, ipd=DZ.ipdPx;
  const r={x:Math.round(b.aT.x-150), y:Math.round(Math.min(b.hT.y,b.aT.y)-0.16*ipd), w:300, h:Math.round(0.30*ipd)};
  const k=3, c=document.createElement('canvas'); c.width=r.w*k; c.height=r.h*k*2; const x=c.getContext('2d'); x.imageSmoothingEnabled=false;
  x.drawImage(DZ.photo,r.x,r.y,r.w,r.h,0,0,r.w*k,r.h*k); x.drawImage(DZ.base,r.x,r.y,r.w,r.h,0,r.h*k,r.w*k,r.h*k);
  window.__cmp=c.toDataURL('image/png'); });
fs.writeFileSync(`${SD}/heal_live.png`, Buffer.from((await page.evaluate(()=>window.__cmp)).split(',')[1],'base64'));
console.log('errors', errs.length?errs:'none');
await browser.close();
