// What a slider tick and a drag actually cost, and that the caches do not change the export.
// Run:  node browlab/tools/e2e/speed.mjs
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
const PHOTO=process.env.BROWLAB_PHOTO||'';
const browser = await chromium.launch({ executablePath: process.env.BROWLAB_CHROME||undefined });
const page = await browser.newPage({ viewport: {width:1180,height:820} });
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
await page.setInputFiles('#fDesign input[type=file]', PHOTO);
await page.click('#fDesign button[type=submit]');
await page.waitForFunction(() => !document.getElementById('dz').hidden && document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:60000});
await page.evaluate(()=>zoomBrows());
const t = await page.evaluate(()=>{
  const time=(n,f)=>{ const t0=performance.now(); for(let i=0;i<n;i++) f(i); return (performance.now()-t0)/n; };
  const out={};
  out.retint_colour = time(20, i=>{ DZ.look.light=(i%9)-4; retint(); });          // colour sliders
  DZ.look.light=0; retint();
  out.retint_weight = time(10, i=>{ DZ.look.weight=5+(i%5)*0.5; retint(); });      // 선 굵기 (first of each is cold)
  out.retint_weight_warm = time(10, i=>{ DZ.look.weight=5+(i%5)*0.5; retint(); }); // the same values again: cached
  DZ.look.weight=5; retint();
  out.render_same = time(20, ()=>dzRender());                                      // nothing changed
  out.render_opacity = time(20, i=>{ DZ.look.opacity=60+(i%20); dzRender(); });     // 강도: layer reused
  out.render_moved = time(20, i=>{ DZ.brows.right.t.x+=(i%2?1:-1); dzRender(); });  // dragging: layer rebuilt
  out.render_wipe = time(20, i=>{ DZ.wipe={on:true,x:0.3+(i%20)*0.02}; dzRender(); });
  DZ.wipe={on:false,x:0.5};
  return out; });
for(const [k,v] of Object.entries(t)) console.log('  '+k.padEnd(20), v.toFixed(2)+' ms');
ok(t.retint_colour<3, 'a colour slider tick is cheap');
ok(t.retint_weight_warm<1.5, 'a repeated 선 굵기 value is cached');
ok(t.render_opacity < t.render_moved*0.8, 'strength/softness reuse the painted layer');
ok(t.render_wipe < t.render_moved*0.8, 'dragging the compare line reuses it too');
// the picture must still be right after all that caching
await page.evaluate(()=>{ DZ.look={...LOOK0}; retint(); DZ.brows=initBrows(); DZ.guides=false; dzRender(); });
await page.waitForTimeout(200);
const shot = await page.evaluate(()=>{ const c=document.getElementById('dzCanvas'); const x=c.getContext('2d'); const d=x.getImageData(0,0,c.width,c.height).data; let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]; return s; });
await page.evaluate(()=>{ DZ.orig=true; dzRender(); });
const plain = await page.evaluate(()=>{ const c=document.getElementById('dzCanvas'); const x=c.getContext('2d'); const d=x.getImageData(0,0,c.width,c.height).data; let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]; return s; });
await page.evaluate(()=>{ DZ.orig=false; DZ.guides=true; dzRender(); });
ok(plain>shot, 'the design still darkens the picture (layer cache did not blank it)');
const exp = await page.evaluate(()=>{ const c=dzExport(); const x=c.getContext('2d'); const b=DZ.brows.right; const d=x.getImageData(Math.round(b.hT.x)-40,Math.round(b.hT.y)-10,80,60).data; let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]; return s/(d.length/4); });
const expPlain = await page.evaluate(()=>{ const c=document.createElement('canvas'); c.width=DZ.W; c.height=DZ.H; const x=c.getContext('2d'); x.drawImage(DZ.photo,0,0); const b=DZ.brows.right; const d=x.getImageData(Math.round(b.hT.x)-40,Math.round(b.hT.y)-10,80,60).data; let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]; return s/(d.length/4); });
ok(exp<expPlain-0.5, `the full-size export still carries the design (${expPlain.toFixed(1)} → ${exp.toFixed(1)})`);
const cold = await page.evaluate(()=>{ DZ.lay=null; DZ.layKey=null; const c=dzExport(); const x=c.getContext('2d'); const b=DZ.brows.right; const d=x.getImageData(Math.round(b.hT.x)-40,Math.round(b.hT.y)-10,80,60).data; let s=0; for(let i=0;i<d.length;i+=4) s+=d[i]; return s/(d.length/4); });
ok(Math.abs(cold-exp)<0.01, 'the export is identical whether the screen layer was warm or cold');
console.log('errors', errs.length?errs:'none');
await browser.close();
