// Nine handles per brow: spacing, the tail pair, symmetry, whole-brow nudges, old designs upgrading.
// Run:  node browlab/tools/e2e/handles.mjs
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
const page = await browser.newPage({ viewport: {width:1400,height:900} });
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
const id = await page.evaluate(async()=>{ const g=await api('api/gallery'); return g.items.find(i=>i.kind==='design').id; });
await page.evaluate(id => openDesign(id), id);
await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
await page.evaluate(()=>{ DZ.brows=initBrows(); DZ.retouch=[]; DZ.base=null; DZ.healed=null; zoomBrows(); dzRender(); });
const keys = await page.evaluate(()=>handlePoints('right').map(h=>h.k).join(','));
ok(keys==='hT,mT,aT,tT,hB,mB,aB,tB,t', 'nine handles per brow: '+keys);
// spacing along the brow is even now
const gaps = await page.evaluate(()=>{ const g=DZ.geo.right, b=DZ.brows.right;
  const along=V.unit(V.sub(g.T,g.H)), at=p=>V.dot(V.sub(p,g.H),along)/V.dist(g.H,g.T);
  return {top:['hT','mT','aT','tT'].map(k=>+at(b[k]).toFixed(2)), tail:+at(b.t).toFixed(2)}; });
console.log('  upper handles along the brow:', gaps.top.join(', '), '· tail', gaps.tail);
const steps = gaps.top.concat([gaps.tail]); let worst=0;
for(let i=1;i<steps.length;i++) worst=Math.max(worst, steps[i]-steps[i-1]);
ok(worst<0.35, `no gap wider than a third of the brow (widest ${worst.toFixed(2)})`);
// the new tail handle bends the outline near the tail without touching the arch
const armBefore = await page.evaluate(()=>{ const g=DZ.geo.right; return {k:{...g.pts[g.iK].gTop}, a:{...g.pts[g.iA].gTop}}; });
const hp = await page.evaluate(()=>{ const {s,tx,ty}=DZ.view, r=document.getElementById('dzStage').getBoundingClientRect();
  const p=handlePoints('right').find(h=>h.k==='tT'); return {x:r.left+p.x*s+tx, y:r.top+p.y*s+ty}; });
await page.mouse.move(hp.x,hp.y); await page.mouse.down(); await page.mouse.move(hp.x,hp.y-22,{steps:6}); await page.mouse.up();
const armAfter = await page.evaluate(()=>{ const g=DZ.geo.right; return {k:{...g.pts[g.iK].gTop}, a:{...g.pts[g.iA].gTop}}; });
const s0 = await page.evaluate(()=>DZ.view.s);
ok((armBefore.k.y-armAfter.k.y)*s0 > 15, `the tail handle lifts the outline there (${((armBefore.k.y-armAfter.k.y)*s0).toFixed(0)} px)`);
ok(Math.abs(armBefore.a.y-armAfter.a.y)*s0 < 3, 'the arch stays where it was');
ok(await page.evaluate(()=>{ const b=DZ.brows.left, m=mirrorPt(DZ.brows.right.tT); return Math.abs(b.tT.x-m.x)<0.01 && Math.abs(b.tT.y-m.y)<0.01; }), 'symmetry mirrors it too');
// whole-brow nudges move every handle by the same amount
const b0 = await page.evaluate(()=>JSON.parse(JSON.stringify(DZ.brows.right)));
await page.click('#dzTabs button[data-t=shape]'); await page.click('#dzPad button[data-n=up]');
const moved = await page.evaluate(b0=>{ const b=DZ.brows.right; const d=Object.keys(b).map(k=>[k, Math.hypot(b[k].x-b0[k].x, b[k].y-b0[k].y)]);
  return {n:d.length, min:Math.min(...d.map(x=>x[1])), max:Math.max(...d.map(x=>x[1]))}; }, b0);
ok(moved.n===9 && moved.max-moved.min<0.01, `▲위로 shifts all ${moved.n} handles equally (${moved.min.toFixed(2)}px)`);
// an older design without the new handles still opens and gains them on the curve
const fresh = await page.evaluate(async id=>{
  const st={tpl:DZ.tplId, brows:{right:{hT:DZ.brows.right.hT, hB:DZ.brows.right.hB, aT:DZ.brows.right.aT, aB:DZ.brows.right.aB, t:DZ.brows.right.t},
                                 left:{hT:DZ.brows.left.hT, hB:DZ.brows.left.hB, aT:DZ.brows.left.aT, aB:DZ.brows.left.aB, t:DZ.brows.left.t}},
            look:DZ.look, sym:true, ipd:62, notes:'old'};
  await post('api/designs/'+id,{state:st});
  await openDesign(id);
  return {keys:handlePoints('right').map(h=>h.k).join(','), notes:DZ.notes}; }, id);
ok(fresh.keys==='hT,mT,aT,tT,hB,mB,aB,tB,t' && fresh.notes==='old', 'a design saved with five handles opens with nine');
await page.evaluate(()=>{ DZ.guides=true; dzRender(); });
await page.screenshot({ path: `${SD}/nine_points.png` });
console.log('errors', errs.length?errs:'none');
await browser.close();
