// The two kinds of saving: the working state saves itself, 시안 저장 keeps a new picture each press.
// Run:  node browlab/tools/e2e/save.mjs
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
const PHOTO=process.env.BROWLAB_PHOTO||'';
const browser = await chromium.launch({ executablePath: process.env.BROWLAB_CHROME||undefined });
const page = await browser.newPage({ viewport: {width:1180,height:820} });
const errs=[]; page.on('pageerror', e => errs.push(String(e)));
const ok=(c,msg)=>console.log((c?'PASS ':'FAIL ')+msg);
await page.goto(URL+'/', { waitUntil: 'networkidle' });
await page.setInputFiles('#fDesign input[type=file]', PHOTO);
await page.fill('#fDesign input[name=client]', '이서연');
await page.click('#fDesign button[type=submit]');
await page.waitForFunction(() => !document.getElementById('dz').hidden && document.getElementById('dzWait').hidden && DZ.tex.right && DZ.photo.naturalWidth, null, {timeout:60000});
const id = await page.evaluate(()=>DZ.id);
await page.click('#dzTabs button[data-t=save]'); await page.waitForTimeout(150);
ok((await page.$eval('#dzShots', e=>e.textContent)).includes('아직 남긴 시안이 없습니다'), 'the save tab starts with no snapshots');
ok((await page.$eval('#dzAuto', e=>e.textContent)).includes('저절로 저장'), 'the autosave line explains itself');
// autosave says saving → saved
await page.evaluate(()=>{ nudge('longer'); });
await page.waitForFunction(()=>document.getElementById('dzAuto').dataset.state==='saved', null, {timeout:8000});
ok(true, 'moving something autosaves the working state');
// two presses of 시안 한 장 저장 = two pictures, numbered
await page.click('#dzSave');
await page.waitForFunction(()=>/시안 1장/.test(document.getElementById('dzSaveNote').textContent), null, {timeout:30000});
await page.evaluate(()=>{ nudge('thicker'); });
await page.click('#dzSave');
await page.waitForFunction(()=>/시안 2장/.test(document.getElementById('dzSaveNote').textContent), null, {timeout:30000});
const d = await fetch(`${URL}/api/designs/${id}`).then(r=>r.json());
ok(d.saves.length===2 && d.saves[0].name==='이서연' && d.saves[1].name==='이서연 2', `two pictures kept, numbered (${d.saves.map(s=>s.name).join(', ')})`);
ok(d.saves[0].image!==d.saves[1].image, 'the second press did not overwrite the first');
const shots = await page.$eval('#dzShots', e=>e.textContent);
ok(/1\. 이서연/.test(shots) && /2\. 이서연 2/.test(shots), `the tab lists them (${shots.replace(/\s+/g,' ').trim()})`);
const gal = await fetch(`${URL}/api/gallery`).then(r=>r.json());
const labels = gal.items.filter(i=>i.saved).map(i=>i.label);
ok(labels.includes('이서연') && labels.includes('이서연 2'), `the gallery shows both (${labels.join(', ')})`);
await page.screenshot({ path: `${SD}/save_tab.png` });
console.log('errors', errs.length?errs:'none');
await browser.close();
