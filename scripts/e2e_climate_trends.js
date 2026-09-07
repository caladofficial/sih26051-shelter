/* E2E for the multi-year climate upgrade (SEC/01 period selector + SEC/10 trends).
   Run: node scripts/e2e_climate_trends.js   (dev server on :8200) */
const { chromium } = require('/tmp/pw/node_modules/playwright-core');
const fs = require('fs');

function resolveChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const base = '/home/user/.cache/ms-playwright';
  const rels = ['chrome-linux64/chrome', 'chrome-linux/chrome',
                'chrome-linux/headless_shell'];
  for (const d of (fs.existsSync(base) ? fs.readdirSync(base) : [])) {
    for (const rel of rels) {
      const c = `${base}/${d}/${rel}`;
      if (fs.existsSync(c)) return c;
    }
  }
  return undefined;
}

const BASE = process.env.BASE || 'http://127.0.0.1:8200';
const results = [];
const check = (name, ok, extra) => {
  results.push({ name, ok });
  console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (extra ? '  — ' + extra : ''));
};

(async () => {
  const browser = await chromium.launch({ executablePath: resolveChrome(), args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|Plotly|cdn/i.test(m.text())) errors.push(m.text());
  });

  await page.goto(`${BASE}/dashboard.html`, { waitUntil: 'networkidle', timeout: 60000 });

  /* ---- SEC/01: period selector is data-driven ---- */
  await page.waitForFunction(
    () => document.querySelector('#year') && document.querySelector('#year').options.length > 1,
    { timeout: 20000 });
  const opts = await page.$$eval('#year option', (o) => o.map((x) => x.value));
  check('period selector populated from /api/climate/coverage', opts.length >= 4, opts.join(','));
  check('default period is the rolling "latest" window',
        await page.$eval('#year', (e) => e.value) === 'latest');
  check('calendar years 2024-2026 selectable',
        ['2024', '2025', '2026'].every((y) => opts.includes(y)));

  /* ---- freshness badge ---- */
  const badge = await page.$eval('#dataFresh', (e) => e.textContent.trim());
  check('freshness badge shows a real date', /\d{4}/.test(badge) && !badge.endsWith('—'), badge);
  check('freshness badge not marked stale',
        !(await page.$eval('#dataFresh', (e) => e.classList.contains('stale'))));

  /* ---- climate load on the rolling window ---- */
  await page.click('#loadClimate');
  await page.waitForFunction(
    () => { const t = document.querySelector('#climateSource'); return t && t.textContent.trim() !== '—'; },
    { timeout: 90000 });
  const src = await page.$eval('#climateSource', (e) => e.textContent.trim());
  check('climate loaded for rolling window', src.length > 2, 'source=' + src);
  const nMetrics = await page.$$eval('#climateSummary .metric', (n) => n.length);
  check('climate summary metrics rendered', nMetrics >= 4, nMetrics + ' metrics');

  /* ---- SEC/10 trends ---- */
  check('SEC/10 nav link present', (await page.$('a[href="#sec10"]')) !== null);
  await page.$eval('#sec10', (e) => e.scrollIntoView());
  await page.click('#trendBtn');
  await page.waitForFunction(
    () => { const w = document.querySelector('#trendWrap'); return w && !w.hidden; },
    { timeout: 60000 });
  check('trend panel revealed', true);

  const tm = await page.$$eval('#trendMetrics .metric', (n) => n.map((x) => x.textContent));
  check('trend headline deltas rendered', tm.length >= 4, tm.length + ' metrics');

  const yearRows = await page.$$eval('#trendYearBody tr', (r) => r.length);
  check('year-by-year table has 3 years', yearRows === 3, yearRows + ' rows');

  const shiftRows = await page.$$eval('#trendShiftBody tr', (r) => r.length);
  check('design-week shift table populated', shiftRows >= 1, shiftRows + ' rows');

  for (const id of ['chartTrendTemp', 'chartTrendStress', 'chartTrendMonthly', 'chartTrendRain']) {
    const ok = await page.$eval('#' + id, (e) => e.classList.contains('has-data') && e.clientHeight > 80);
    check(`chart ${id} rendered`, ok);
  }

  const note = await page.$eval('#trendNote', (e) => e.textContent.trim());
  check('like-for-like comparison note shown', /day-of-year/i.test(note), note.slice(0, 70));

  /* ---- switching to a fixed year still works ---- */
  await page.selectOption('#year', '2024');
  await page.$eval('#sec1', (e) => e.scrollIntoView());
  await page.click('#loadClimate');
  await page.waitForTimeout(4000);
  const nH = await page.evaluate(() => window.__none);   // noop
  check('no uncaught page errors', errors.length === 0, errors.slice(0, 3).join(' | '));

  await page.screenshot({ path: '/home/user/trends_sec10.png', fullPage: false });
  await page.$eval('#sec10', (e) => e.scrollIntoView());
  await page.waitForTimeout(1200);
  await page.screenshot({ path: '/home/user/trends_sec10_view.png' });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
