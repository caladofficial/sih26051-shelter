/* E2E (new-feature layer): preset filter + zone-only, VS-STUDIO thermal compare, PNG snap, offline facts.
   Run against a local API+static server on :8010 (see README/other e2e scripts). */
const { chromium } = require('/tmp/pw/node_modules/playwright-core');
const fs = require('fs');
function resolveChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const cands = [
    '/home/user/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',
    '/home/user/.cache/ms-playwright/chromium-1169/chrome-linux/chrome',
    '/home/user/.local/share/choreographer/deps/chrome-linux64/chrome',
  ];
  for (const p of cands) if (fs.existsSync(p)) return p;
  const shell = '/home/user/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell';
  if (fs.existsSync(shell)) return shell;
  return null;
}
const CHROME = resolveChrome();
const results = [];
function check(name, ok, extra) {
  results.push({ name, ok });
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  ' + extra : ''));
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text()); });

  await page.goto('http://localhost:8010/dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const c = document.getElementById('presetCount');
    return c && /^18 \/ 18 PRESETS$/.test(c.textContent);
  }, { timeout: 30000 });

  // F1: count chip shows 18/18
  const total = await page.evaluate(() => document.getElementById('presetCount').textContent);
  check('F1: preset count shows 18/18', total === '18 / 18 PRESETS', total);

  // F2: text filter narrows the grid (cards hidden, count updates)
  await page.fill('#presetFilter', 'cyclone');
  await page.waitForTimeout(400);
  const f2 = await page.evaluate(() => ({
    count: document.getElementById('presetCount').textContent,
    cards: [...document.querySelectorAll('#presetGrid .preset-card')].map((c) => c.querySelector('.preset-card-hd b').textContent),
  }));
  check('F2: filter "cyclone" leaves exactly the cyclone card',
    f2.count.startsWith('1 / 18') && f2.cards.length === 1 && /Cyclone/.test(f2.cards[0]), JSON.stringify(f2));

  // F3: zone-only shows only the 6 RECOMMENDED (composite) cards at Prayagraj
  await page.fill('#presetFilter', '');
  await page.check('#presetZoneOnly');
  await page.waitForTimeout(400);
  const f3 = await page.evaluate(() => ({
    count: document.getElementById('presetCount').textContent,
    hd: [...document.querySelectorAll('#presetGrid .preset-group-hd')].map((e) => e.textContent),
  }));
  check('F3: zone-only at Prayagraj shows 6 composite-recommended only',
    f3.count === '6 / 18 PRESETS' && f3.hd.length === 1 && /RECOMMENDED · COMPOSITE/.test(f3.hd[0]), JSON.stringify(f3));
  await page.uncheck('#presetZoneOnly');

  // F4: VS STUDIO compare opens and fills with real numbers
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    const cmp = document.querySelector('#presetGrid [data-compare]'); // first card = Composite Brick Studio
    cmp.click();
  });
  await page.waitForFunction(() => {
    const w = document.getElementById('cmpWrap');
    return w && !w.hidden && /STUDIO · LIVE RUN/.test(document.getElementById('cmpHead').textContent) &&
      document.getElementById('cmpBody').querySelectorAll('tr').length >= 8;
  }, { timeout: 60000 });
  const f4 = await page.evaluate(() => ({
    title: document.getElementById('cmpTitle').textContent,
    rows: [...document.querySelectorAll('#cmpBody tr')].map((tr) => tr.children[1]?.textContent + ' vs ' + tr.children[2]?.textContent),
  }));
  check('F4: compare panel title carries site + preset', /PRAYAGRAJ/.test(f4.title) && /COMPOSITE BRICK STUDIO/.test(f4.title), f4.title);
  const numeric = f4.rows.some((r) => /°C/.test(r) || /%/.test(r));
  check('F5: compare rows carry engine numbers (not dashes)', numeric && f4.rows.length >= 8, f4.rows.slice(0, 3).join(' | '));
  const note = await page.evaluate(() => document.getElementById('cmpNote').textContent);
  check('F6: verdict line present', /wins|better|verified/.test(note), note.slice(0, 140));

  // F7: LOAD preset button inside compare loads design into studio
  await page.evaluate(() => document.getElementById('cmpLoadPreset').click());
  await page.waitForTimeout(2500);
  const f7 = await page.evaluate(() => document.getElementById('structSource').textContent);
  check('F7: compare → load preset drives the studio', /^PRESET · PRAYAGRAJ_BRICK/.test(f7), f7);

  // F8: PNG snapshot downloads a real (non-blank) image
  const dlPromise = page.waitForEvent('download', { timeout: 30000 });
  await page.evaluate(() => document.getElementById('snap3d').click());
  const dl = await dlPromise;
  const p = '/tmp/snap_check.png';
  await dl.saveAs(p);
  const size = fs.statSync(p).size;
  check('F8: 3D snapshot PNG downloads non-blank', size > 5000, size + ' bytes');

  // F9: offline modal facts show 18 presets
  await page.click('#offlineBtn');
  await page.waitForTimeout(600);
  const f9 = await page.evaluate(() => ({
    presets: document.getElementById('offPresets').textContent,
    sites: document.getElementById('offSites').textContent,
    mats: document.getElementById('offMats').textContent,
  }));
  check('F9: offline modal facts = 15 sites / 15 mats / 18 presets',
    f9.sites === '15' && f9.mats === '15' && f9.presets === '18', JSON.stringify(f9));

  check('no page JS errors', errors.length === 0, errors.join(' | ').slice(0, 200));
  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(failed.length === 0 ? '=== FEATURES ALL PASS (' + results.length + ' checks) ===' : '=== FEATURES FAILED: ' + failed.length + ' ===');
  process.exit(failed.length ? 1 : 0);
})();
