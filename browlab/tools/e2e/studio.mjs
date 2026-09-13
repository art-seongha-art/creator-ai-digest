// The consultation studio, end to end: a photo becomes a design, is saved, and reopens.
// Run:  node browlab/tools/e2e/studio.mjs
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
const ctx = await browser.newContext({ viewport: {width:1400,height:900}, acceptDownloads: true });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error' && !/ERR_TUNNEL|net::/.test(m.text())) errs.push(m.text()); });
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
page.on('response', async r => { if (r.status()>=400 && r.url().includes('/api/')) console.log('HTTP', r.status(), r.url().replace(URL,''), await r.text().catch(()=>'')); });
page.on('request', r => { if (r.url().endsWith('/api/jobs') && r.method()==='POST') console.log('POST /api/jobs body:', (r.postData()||'').slice(0,240)); });
await page.goto(URL+'/', { waitUntil: 'networkidle' });
await page.waitForTimeout(400);
ok(await page.$eval('#fDesign', e=>e.classList.contains('on')), 'design panel is the first view');

// 1. start a consultation from the form with the selfie
await page.setInputFiles('#fDesign input[type=file]', PHOTO);
await page.fill('#fDesign input[name=client]', '이서연');
const t0=Date.now();
await page.click('#fDesign button[type=submit]');
await page.waitForFunction(() => !document.getElementById('dz').hidden && document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:60000});
console.log('form → studio in', Date.now()-t0, 'ms');
ok(await page.$eval('#dzClient', e=>e.value)==='이서연', 'client name carried into the studio');
const id = await page.evaluate(()=>DZ.id);
ok(!!DZ_placement(await page.evaluate(()=>DZ.lm&&DZ.lm.source)), 'face measured by '+await page.evaluate(()=>DZ.lm&&DZ.lm.source));
function DZ_placement(x){ return x; }

// 2. symmetry: drag the right arch-top handle, the left follows as its mirror
const before = await page.evaluate(()=>JSON.stringify(DZ.brows.left.aT));
const h = await page.evaluate(() => { const {s,tx,ty}=DZ.view; const r=document.getElementById('dzStage').getBoundingClientRect();
  const p=handlePoints('right').find(h=>h.k==='aT'); return {x:r.left+p.x*s+tx, y:r.top+p.y*s+ty}; });
await page.mouse.move(h.x, h.y); await page.mouse.down(); await page.mouse.move(h.x, h.y-25, {steps:5}); await page.mouse.up();
const after = await page.evaluate(()=>({l:DZ.brows.left.aT, r:DZ.brows.right.aT, m:mirrorPt(DZ.brows.right.aT)}));
ok(before!==JSON.stringify(after.l) && Math.abs(after.l.x-after.m.x)<0.01 && Math.abs(after.l.y-after.m.y)<0.01, 'symmetry mirrors the dragged handle');
// body drag moves the whole brow
const body = await page.evaluate(() => { const {s,tx,ty}=DZ.view; const r=document.getElementById('dzStage').getBoundingClientRect();
  const g=DZ.geo.right; const p=g.pts[20].c; return {x:r.left+p.x*s+tx, y:r.top+p.y*s+ty}; });
const tBefore = await page.evaluate(()=>({...DZ.brows.right.t}));
await page.mouse.move(body.x, body.y); await page.mouse.down(); await page.mouse.move(body.x+20, body.y+10, {steps:5}); await page.mouse.up();
const tAfter = await page.evaluate(()=>({...DZ.brows.right.t}));
const s = await page.evaluate(()=>DZ.view.s);
ok(Math.abs((tAfter.x-tBefore.x)*s-20)<1.5 && Math.abs((tAfter.y-tBefore.y)*s-10)<1.5, 'dragging the body moves the whole brow');
// sym off, nudge only affects the active side
await page.click('#dzSym'); await page.click('#dzTabs button[data-t=shape]');
const lt = await page.evaluate(()=>JSON.stringify(DZ.brows.left.t));
await page.click('#dzPad button[data-n=longer]');
ok(lt===await page.evaluate(()=>JSON.stringify(DZ.brows.left.t)), 'with symmetry off a nudge leaves the other brow alone');
await page.click('#dzSym');

// 2b. seven handles, wipe, tap toggle, undo
ok(await page.evaluate(()=>handlePoints('right').map(h=>h.k).join(','))==='hT,mT,aT,tT,hB,mB,aB,tB,t', 'nine handles per brow');
const mid = await page.evaluate(() => { const {s,tx,ty}=DZ.view; const r=document.getElementById('dzStage').getBoundingClientRect();
  const p=handlePoints('right').find(h=>h.k==='mT'); return {x:r.left+p.x*s+tx, y:r.top+p.y*s+ty}; });
const thickBefore = await page.evaluate(()=>{ const g=DZ.geo.right; return V.dist(g.pts[g.iM].gTop, g.pts[g.iM].gBot); });
await page.mouse.move(mid.x, mid.y); await page.mouse.down(); await page.mouse.move(mid.x, mid.y-18, {steps:5}); await page.mouse.up();
const thickAfter = await page.evaluate(()=>{ const g=DZ.geo.right; return V.dist(g.pts[g.iM].gTop, g.pts[g.iM].gBot); });
ok(thickAfter>thickBefore+10, `dragging the middle-top handle up thickens the body there (${thickBefore.toFixed(1)} → ${thickAfter.toFixed(1)} px)`);
const histLen = await page.evaluate(()=>DZ.hist.length);
await page.keyboard.press('Control+z'); await page.waitForTimeout(150);
const thickUndo = await page.evaluate(()=>{ const g=DZ.geo.right; return V.dist(g.pts[g.iM].gTop, g.pts[g.iM].gBot); });
ok(Math.abs(thickUndo-thickBefore)<0.5 && histLen>=2, 'Ctrl+Z undoes the drag');
await page.click('#dzRedo'); await page.waitForTimeout(150);
ok(Math.abs(await page.evaluate(()=>{ const g=DZ.geo.right; return V.dist(g.pts[g.iM].gTop, g.pts[g.iM].gBot); })-thickAfter)<0.5, '↷ redoes it');
// wipe: brows only right of the line
await page.click('#dzWipe'); await page.evaluate(()=>{ DZ.guides=false; dzRender(); });   // measure pixels without the white handles
const stage = await page.$eval('#dzStage', e=>{ const b=e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; });
const darkness = async (x0,x1) => page.evaluate(([x0,x1])=>{ const c=document.getElementById('dzCanvas'), d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; const W=c.width; let s=0,n=0; for(let y=0;y<c.height;y++) for(let x=Math.round(x0*W);x<Math.round(x1*W);x++){ const i=(y*W+x)*4; s+=d[i]; n++; } return s/n; }, [x0,x1]);
const withWipeL = await darkness(0.05,0.45), withWipeR = await darkness(0.55,0.95);
await page.evaluate(()=>{ DZ.orig=true; dzRender(); });
const origL = await darkness(0.05,0.45), origR = await darkness(0.55,0.95);
await page.evaluate(()=>{ DZ.orig=false; DZ.guides=true; dzRender(); });
ok(Math.abs(withWipeL-origL)<0.05 && withWipeR<origR-0.15, `wipe on: left half is the original, right half carries the design (Δleft ${(withWipeL-origL).toFixed(2)}, Δright ${(withWipeR-origR).toFixed(2)})`);
await page.mouse.move(stage.x+stage.w*0.5, stage.y+stage.h*0.6); await page.mouse.down(); await page.mouse.move(stage.x+stage.w*0.8, stage.y+stage.h*0.6, {steps:6}); await page.mouse.up();
ok(Math.abs(await page.evaluate(()=>DZ.wipe.x)-0.8)<0.02, 'the divider drags');
await page.click('#dzWipe');
// a tap on empty skin flips before/after
await page.mouse.click(stage.x+stage.w*0.5, stage.y+stage.h*0.85);
ok(await page.evaluate(()=>DZ.orig===true), 'tap on empty skin hides the design');
await page.mouse.click(stage.x+stage.w*0.5, stage.y+stage.h*0.85);
ok(await page.evaluate(()=>DZ.orig===false), 'tap again brings it back');
await page.click('#dzPts'); ok(await page.evaluate(()=>!DZ.guides && !document.getElementById('dzGuides').checked), '점 button hides the handles'); await page.click('#dzPts');
await page.click('#dzLines'); ok(await page.evaluate(()=>DZ.lines), '가이드선 toggles'); await page.screenshot({ path: `${SD}/e2e_lines.png` }); await page.click('#dzLines');
const cmp = await page.evaluate(()=>{ const c=compareCanvas(); return [c.width,c.height]; });
ok(cmp[0]>cmp[1]*2 && cmp[1]>100, `before/after strip is side by side (${cmp[0]}×${cmp[1]})`);

// 2c. the heal brush: stroke across the natural tail hair, skin gets lighter there; undo brings it back
ok(await page.evaluate(()=>DZ.look.density===1&&DZ.look.weight===5), '선 진하기 1 · 선 굵기 5 are the defaults');
const defs = await page.$$eval('#dzLookSliders input', es=>es.map(e=>[e.min,e.max,e.step,e.value]));
ok(defs[2].join('/')==='0.2/2/0.1/1' && defs[3].join('/')==='0/10/0.5/5', 'slider ranges centre on as-drawn ('+defs.slice(2).map(d=>d.join('/')).join(' ; ')+')');
await page.click('#dzHeal');
ok(await page.evaluate(()=>DZ.tool==='heal' && !document.getElementById('dzHealBox').hidden), 'heal mode shows its box');
const tailSeg = await page.evaluate(()=>{ const g=DZ.geo.right; const a=g.pts[34].c, b=g.pts[38].c; return {a,b}; });
const lum = async (a,b)=>page.evaluate(([a,b])=>{ const src=DZ.base||DZ.photo; const c=document.createElement('canvas'); const x0=Math.min(a.x,b.x)-6,y0=Math.min(a.y,b.y)-6,w=Math.abs(a.x-b.x)+12,h=Math.abs(a.y-b.y)+12; c.width=w; c.height=h; const x=c.getContext('2d'); x.drawImage(src,x0,y0,w,h,0,0,w,h); const d=x.getImageData(0,0,w,h).data; let s=0; for(let i=0;i<d.length;i+=4) s+=0.299*d[i]+0.587*d[i+1]+0.114*d[i+2]; return s/(d.length/4); }, [a,b]);
const lumBefore = await lum(tailSeg.a, tailSeg.b);
const S = await page.evaluate(([a,b])=>{ const {s,tx,ty}=DZ.view; const r=document.getElementById('dzStage').getBoundingClientRect(); return [{x:r.left+a.x*s+tx,y:r.top+a.y*s+ty},{x:r.left+b.x*s+tx,y:r.top+b.y*s+ty}]; }, [tailSeg.a, tailSeg.b]);
await page.mouse.move(S[0].x,S[0].y); await page.mouse.down(); await page.mouse.move(S[1].x,S[1].y,{steps:8}); await page.mouse.up(); await page.waitForTimeout(200);
const lumAfter = await lum(tailSeg.a, tailSeg.b);
ok(await page.evaluate(()=>DZ.retouch.length===1 && !!DZ.base), 'a stroke is kept and the retouched photo exists');
ok(lumAfter>lumBefore+1.5, `the stroke lightens the hair it crossed (${lumBefore.toFixed(1)} → ${lumAfter.toFixed(1)})`);
await page.keyboard.press('Control+z'); await page.waitForTimeout(150);
ok(await page.evaluate(()=>DZ.retouch.length===0 && !DZ.base), 'undo removes the stroke');
await page.click('#dzRedo'); await page.waitForTimeout(150);
ok(await page.evaluate(()=>DZ.retouch.length===1 && !!DZ.base), 'redo replays it');
await page.screenshot({ path: `${SD}/e2e_heal.png` });
await page.click('#dzHeal'); ok(await page.evaluate(()=>DZ.tool==='move'), 'heal mode toggles off');

// 3. switch template, change look, hold original
await page.click('#dzTabs button[data-t=tpl]');
const tpls = await page.$$('.dz-tpl');
await tpls[2].click(); await page.waitForTimeout(300);
ok(await page.evaluate(()=>DZ.tplId===DZ.tpls[2].id && !!DZ.tex.left), 'template switch re-analyses both sides');
await page.click('#dzTabs button[data-t=look]');
await page.click('#dzStages button:nth-child(2)');
ok(await page.evaluate(()=>DZ.look.opacity===68&&DZ.look.blur===40), 'stage preset sets strength and softness');
await page.click('#dzColors button:nth-child(3)');
ok(await page.evaluate(()=>DZ.look.color==='ash'), 'pigment swatch selects');
// (right half only: the heal stroke lightened skin on the other side, and no handles)
const redRight = () => page.evaluate(()=>{ const c=document.getElementById('dzCanvas'), d=c.getContext('2d').getImageData(0,0,c.width,c.height).data, W=c.width; let s=0; for(let y=0;y<c.height;y++) for(let x=W>>1;x<W;x++) s+=d[(y*W+x)*4]; return s; });
await page.evaluate(()=>{ DZ.guides=false; dzRender(); });
const pxBefore = await redRight();
await page.evaluate(()=>{ DZ.orig=true; dzRender(); });
const pxOrig = await redRight();
await page.evaluate(()=>{ DZ.orig=false; DZ.guides=true; dzRender(); });
ok(pxOrig>pxBefore, 'holding 원본 removes the darker strokes');

// 4. upload a template through the UI (a cut PNG)
await page.click('#dzTabs button[data-t=tpl]');
const n0 = await page.evaluate(()=>DZ.tpls.length);
await page.setInputFiles('#dzFile', 'browlab/templates/spine6_right.png');
await page.waitForFunction(n=>DZ.tpls.length===n+1, n0, {timeout:15000});
ok(await page.evaluate(()=>DZ.tpls[DZ.tpls.length-1].name==='spine6_right' && DZ.tplId===DZ.tpls[DZ.tpls.length-1].id), 'uploaded template is added and selected');

// 5. autosave, save to gallery, download, print
await page.waitForTimeout(1200);
const st = await fetch(`${URL}/api/designs/${id}`).then(r=>r.json());
ok(st.state && st.state.tpl===await page.evaluate(()=>DZ.tplId) && st.state.brows.right.t, 'state autosaved on the server');
await page.click('#dzTabs button[data-t=save]');
await page.fill('#dzNotes', '꼬리 3mm 연장');
await page.click('#dzSave');
await page.waitForFunction(()=>/시안 \d+장/.test(document.getElementById('dzSaveNote').textContent), null, {timeout:30000});
const d1 = await fetch(`${URL}/api/designs/${id}`).then(r=>r.json());
ok(d1.saves.length===1 && d1.saves[0].name==='이서연' && d1.saves[0].settings.notes==='꼬리 3mm 연장', 'saved under the client name with the look');
const [dl] = await Promise.all([page.waitForEvent('download', {timeout:5000}).catch(()=>null), page.click('#dzDownload')]);
console.log('download event:', dl ? await dl.suggestedFilename() : 'none (headless)');
const bytes = await page.evaluate(()=>dzExport().toDataURL('image/jpeg',0.93).length);
ok(bytes>200000, 'export produces a full-size picture ('+Math.round(bytes/1024)+' KB base64)');
await page.click('#dzPrint');
let sheet=null; for(let k=0;k<40&&!sheet;k++){ await page.waitForTimeout(500); const jobs=await fetch(`${URL}/api/jobs`).then(r=>r.json()); sheet=jobs.jobs.find(j=>j.kind==='sheet'&&j.params.photo_from&&j.params.photo_from.job===id); }
ok(!!sheet && sheet.params.photo_from && sheet.params.photo_from.job===id, '1:1 출력 시트 job started from the saved picture');

// 6. close → gallery → reopen restores
await page.click('#dzClose'); await page.waitForTimeout(600);
ok(await page.$eval('#dz', e=>e.hidden), 'studio closes');
await page.evaluate(()=>setView('gallery')); await page.waitForTimeout(600);
const labels = await page.$$eval('#gallery .gcard .meta b', es=>es.map(e=>e.textContent));
ok(labels.includes('이서연'), 'gallery lists the consultation ('+labels.slice(0,4).join(', ')+')');
await page.screenshot({ path: `${SD}/e2e_gallery.png` });
await page.evaluate(()=>{ const i=items.findIndex(it=>it.kind==='design'&&it.saved); openLb(i); }); await page.waitForTimeout(500);
await page.screenshot({ path: `${SD}/e2e_lb.png` });
const hasBtn = await page.$$eval('#lbSide .acts button', bs=>bs.map(b=>b.textContent));
ok(hasBtn.includes('상담 이어하기'), 'lightbox offers 상담 이어하기 ('+hasBtn.join(', ')+')');
ok(await page.$eval('#lbCmp', e=>!e.hidden), 'a saved design opens with the before/after compare');
ok((await page.$$eval('#lbSide dt', es=>es.map(e=>e.textContent))).includes('도안'), 'the side panel lists the look (도안, 색소…)');
await page.click('#lbSide .acts button:has-text("상담 이어하기")');
await page.waitForFunction(() => !document.getElementById('dz').hidden && document.getElementById('dzWait').hidden && DZ.tex.right, null, {timeout:30000});
const re = await page.evaluate(()=>({tpl:DZ.tplId, notes:DZ.notes, look:DZ.look.color, t:DZ.brows.right.t, strokes:DZ.retouch.length, base:!!DZ.base}));
ok(re.tpl===st.state.tpl && re.notes==='꼬리 3mm 연장' && re.look==='ash' && Math.abs(re.t.x-st.state.brows.right.t.x)<0.01, 'reopening restores template, look, notes and handles');
ok(re.strokes===1 && re.base, 'reopening replays the retouch stroke');
await page.screenshot({ path: `${SD}/e2e_reopen.png` });
await page.keyboard.press('Escape'); await page.waitForTimeout(300);
ok(await page.$eval('#dz', e=>e.hidden), 'Esc closes the studio');

// 7. phone: pinch zoom via CDP touch events
const phone = await ctx.newPage(); await phone.setViewportSize({width:430,height:900});
phone.on('pageerror', e => errs.push('phone: '+String(e)));
await phone.goto(URL+'/', { waitUntil: 'networkidle' });
await phone.evaluate(id => openDesign(id), id);
await phone.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
await phone.waitForTimeout(300);
await phone.screenshot({ path: `${SD}/e2e_phone.png` });
const s0 = await phone.evaluate(()=>DZ.view.s);
const cdp = await ctx.newCDPSession(phone);
const r = await phone.$eval('#dzStage', e=>{ const b=e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; });
const cx=r.x+r.w/2, cy=r.y+r.h/2;
await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:cx-30,y:cy,id:1},{x:cx+30,y:cy,id:2}]});
for(let k=1;k<=6;k++) await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:cx-30-k*12,y:cy,id:1},{x:cx+30+k*12,y:cy,id:2}]});
await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
await phone.waitForTimeout(200);
const s1 = await phone.evaluate(()=>DZ.view.s);
ok(s1>s0*1.5, `pinch zooms the view (${s0.toFixed(2)} → ${s1.toFixed(2)})`);
await phone.screenshot({ path: `${SD}/e2e_phone_pinch.png` });
await phone.click('#dzZoomBrow'); await phone.waitForTimeout(200);
await phone.screenshot({ path: `${SD}/e2e_phone_brows.png` });
console.log('JS errors:', errs.length ? errs : 'none');
await browser.close();
