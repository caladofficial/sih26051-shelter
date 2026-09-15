/* E2E: NLP is fully wired into the CAD/studio — say it, and the design changes.
   Run: node scripts/e2e_nlp_cad.js   (dev server on :8010) */
const { chromium } = require('/tmp/pw/node_modules/playwright-core');
const fs = require('fs');

function resolveChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cands = [
    '/home/user/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',
    '/home/user/.cache/ms-playwright/chromium-1169/chrome-linux/chrome',
  ];
  for (const p of cands) if (fs.existsSync(p)) return p;
  const shell = '/home/user/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell';
  if (fs.existsSync(shell)) return shell;
  const fs0 = require('fs');
  try {
    for (const d of fs0.readdirSync('/home/user/.cache/ms-playwright')) {
      for (const sub of ['chrome-linux/headless_shell',
                         'chrome-headless-shell-linux64/chrome-headless-shell']) {
        const p = `/home/user/.cache/ms-playwright/${d}/${sub}`;
        if (fs0.existsSync(p)) return p;
      }
    }
  } catch (e) {}
  return null;
}
const CHROME = resolveChrome();
const BASE = process.env.BASE || 'http://localhost:8010';
const results = [];
function check(name, ok, extra) {
  results.push({ name, ok });
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  ' + extra : ''));
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text()); });

  await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const s = document.getElementById('location');
    return s && s.options.length >= 15;
  }, { timeout: 30000 });
  await page.waitForFunction(() => document.getElementById('wallMat').options.length > 5, { timeout: 15000 });

  async function ask(text) {
    await page.fill('#nlpInput', text);
    await page.click('#nlpBtn');
    await page.waitForFunction(() =>
      /understood as|not actionable|ASSISTANT/.test(document.getElementById('nlpStatus').textContent),
      { timeout: 90000 });
  }
  const form = () => page.evaluate(() => ({
    wall: document.getElementById('wallMat').value,
    roof: document.getElementById('roofMat').value,
    ins: document.getElementById('insMat').value,
    ach: document.getElementById('ventAch').value,
    pitch: document.getElementById('roofPitch').value,
    len: document.getElementById('length').value,
    wid: document.getElementById('width').value,
  }));

  // give the studio a distinctive state so we can prove modify *changes* it
  // rather than restarting from the zone prescription
  await page.selectOption('#roofMat', 'timber');
  await page.selectOption('#insMat', 'none');
  await page.selectOption('#ventAch', '4');

  // C1: "change the walls to plywood" auto-applies to the studio form
  const wallBefore = (await form()).wall;
  await ask('change the walls to plywood');
  const f1 = await form();
  check('C1: wall material auto-applied to the studio', f1.wall === 'plywood' && f1.wall !== wallBefore, `before=${wallBefore} after=${f1.wall}`);

  // C2: the modify kept everything else (context carried from the studio)
  check('C2: modify keeps roof/insulation/ventilation',
    f1.roof === 'timber' && f1.ins === 'none' && f1.ach === '4',
    JSON.stringify({ roof: f1.roof, ins: f1.ins, ach: f1.ach }));

  // C3: the 3D legend names the new material (CAD visibly reflects it)
  await page.waitForFunction(() => {
    const l = document.getElementById('structLegend');
    return l && /PLYWOOD/.test(l.textContent);
  }, { timeout: 30000 });
  const legend = await page.evaluate(() => document.getElementById('structLegend').textContent);
  check('C3: 3D legend shows WALL · PLYWOOD', /WALL\s*·\s*PLYWOOD/i.test(legend), legend.replace(/\s+/g, ' ').trim());

  // C4: multi-turn "make it bigger" continues from the plywood design
  await ask('make it bigger');
  const f4 = await form();
  check('C4: multi-turn keeps plywood + grows footprint',
    f4.wall === 'plywood' && f4.roof === 'timber' && parseFloat(f4.len) > 3.0,
    JSON.stringify({ wall: f4.wall, len: f4.len, wid: f4.wid }));

  // C5: a fresh design with a named site does NOT leak the previous materials
  await ask('now design a stone shelter for Leh');
  const f5 = await form();
  check('C5: fresh named site starts from its own prescription',
    f5.wall === 'stone' && f5.wall !== 'plywood',
    JSON.stringify({ wall: f5.wall, roof: f5.roof }));

  // C6: roof pitch phrase also lands in the studio + 3D
  await ask('sloped roof for monsoon in Mumbai');
  const f6 = await form();
  check('C6: sloped roof sets the studio pitch control',
    parseFloat(f6.pitch) > 0, `pitch=${f6.pitch}`);

  check('no page JS errors', errors.length === 0, errors.join(' | ').slice(0, 300));

  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\n=== ${failed.length} FAIL ===` : `\n=== NLP-CAD ALL PASS (${results.length} checks) ===`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('E2E CRASH:', e.message); process.exit(1); });
