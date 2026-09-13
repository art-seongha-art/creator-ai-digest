// 전체화면 keeps the working panel, and survives the browser cancelling its own fullscreen.
// Run:  node browlab/tools/e2e/fullscreen.mjs
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
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
for (const [name, vp] of [['ipad',{width:1180,height:820}],['phone',{width:430,height:900}]]) {
  const page = await browser.newPage({ viewport: vp });
  const errs=[]; page.on('pageerror', e => errs.push(String(e)));
  await page.goto(URL+'/', { waitUntil: 'networkidle' });
  const id = await page.evaluate(async()=>{ const g=await api('api/gallery'); return g.items.find(i=>i.kind==='design').id; });
  await page.evaluate(id => openDesign(id), id);
  await page.waitForFunction(() => document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:30000});
  const area = () => page.$eval('#dzStage', e=>{ const b=e.getBoundingClientRect(); return b.width*b.height; });
  const h0 = await area();
  await page.click('#dzFull'); await page.waitForTimeout(300);
  const h1 = await area();
  ok(h1>h0*1.03, `${name}: 전체화면 grows the picture (${Math.round(h0/1000)}k → ${Math.round(h1/1000)}k px²)`);
  ok(await page.evaluate(()=>!document.getElementById('dzExit').hidden), `${name}: an exit button is on the stage`);
  const panel = await page.$eval('.dz-side', e=>{ const b=e.getBoundingClientRect(); return {w:b.width,h:b.height}; });
  ok(panel.w>200 && panel.h>40, `${name}: the working panel is still there (${Math.round(panel.w)}×${Math.round(panel.h)})`);
  ok(await page.$$eval('#dzTabs button', bs=>bs.filter(b=>b.offsetParent!==null).length>=4), `${name}: its tabs are reachable`);
  // the browser dropping its own fullscreen (what a swipe does on an iPad) must not undo ours
  await page.evaluate(async()=>{ if(document.exitFullscreen&&document.fullscreenElement) await document.exitFullscreen(); document.dispatchEvent(new Event('fullscreenchange')); });
  await page.waitForTimeout(300);
  const h2 = await area();
  ok(Math.abs(h2-h1)<2 && await page.evaluate(()=>document.getElementById('dz').classList.contains('immersive')), `${name}: a cancelled browser fullscreen leaves ours standing`);
  await page.screenshot({ path: `${SD}/imm_${name}.png` });
  await page.keyboard.press('Escape'); await page.waitForTimeout(300);
  ok(await page.evaluate(()=>!document.getElementById('dz').classList.contains('immersive') && !document.getElementById('dz').hidden), `${name}: Esc leaves 전체화면 without closing the studio`);
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  ok(await page.$eval('#dz', e=>e.hidden), `${name}: Esc again closes the studio`);
  console.log(' ', name, 'errors', errs.length?errs:'none');
  await page.close();
}
await browser.close();
