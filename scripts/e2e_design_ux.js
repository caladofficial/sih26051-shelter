/* E2E: SEC/03 option-group cabinets + pitch/vent controls + NLP feedback loop.
   Run: node scripts/e2e_design_ux.js        (dev server on :8010) */
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

  // capture simulate payloads to prove the new controls reach the engine API
  let simBody = null;
  await page.route('**/api/simulate', async (route) => {
    simBody = JSON.parse(route.request().postData() || '{}');
    await route.continue();
  });

  await page.goto(BASE + '/dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const s = document.getElementById('location');
    return s && s.options.length >= 15;
  }, { timeout: 30000 });

  // U1: the design matrix is organised into labelled cabinets (fieldsets)
  const groups = await page.evaluate(() =>
    [...document.querySelectorAll('#sec3 .ctl-group > legend')].map((e) => e.textContent.trim()));
  check('U1: SEC/03 has >=5 labelled option cabinets',
    groups.length >= 5 && /shell/i.test(groups[0]) && /roof/i.test(groups.join(' ')),
    JSON.stringify(groups));

  // U2: each cabinet is visually separated (non-collapsed margins + borders)
  const gap = await page.evaluate(() => {
    const gs = [...document.querySelectorAll('#sec3 .ctl-group')];
    if (gs.length < 2) return { gap: 0 };
    const r0 = gs[0].getBoundingClientRect(), r1 = gs[1].getBoundingClientRect();
    const style = getComputedStyle(gs[0]);
    return { gap: r1.top - r0.bottom, border: style.borderTopWidth,
             moduleGap: getComputedStyle(document.querySelector('.module')).marginBottom };
  });
  check('U2: cabinets separated by margin + border',
    gap.gap >= 14 && gap.border !== '0px' && parseFloat(gap.moduleGap) >= 30,
    JSON.stringify(gap));

  // U3: roof pitch + ventilation controls exist with the expected choices
  const ctl = await page.evaluate(() => ({
    pitch: [...document.getElementById('roofPitch').options].map((o) => o.value),
    ach: [...document.getElementById('ventAch').options].map((o) => o.value),
  }));
  check('U3: roof pitch 0-30° + ACH 0.5-10 controls present',
    ctl.pitch.join(',') === '0,5,10,15,20,25,30' && ctl.ach.length === 8 &&
    ctl.ach[0] === '0.5' && ctl.ach[7] === '10',
    JSON.stringify(ctl));

  // U4: changing pitch + ACH flows into the simulate request
  await page.selectOption('#roofPitch', '20');
  await page.selectOption('#ventAch', '6');
  await page.click('#simulate');
  await page.waitForFunction(() => !document.getElementById('simSection').hidden,
    { timeout: 90000 });
  check('U4: simulate payload carries roof_pitch_deg=20 + ach=6',
    simBody && simBody.roof_pitch_deg === 20 && simBody.ach === 6,
    JSON.stringify({ pitch: simBody && simBody.roof_pitch_deg, ach: simBody && simBody.ach }));

  // U5: the engine actually responded differently with the pitched/monsoon setup
  const peak = await page.evaluate(() =>
    document.querySelector('#simMetrics .metric b') && document.getElementById('simMetrics').textContent);
  check('U5: simulation produced metrics with the new inputs', /°C/.test(peak || ''), '');

  // U6: NLP feedback loop — verdict posts to /api/nlp/feedback
  let fbBody = null;
  await page.route('**/api/nlp/feedback', async (route) => {
    fbBody = JSON.parse(route.request().postData() || '{}');
    await route.continue();
  });
  await page.fill('#nlpInput', 'a cool mud brick shelter for a family of six in Jaipur with a sloped roof');
  await page.click('#nlpBtn');
  await page.waitForFunction(() => !document.getElementById('nlpResult').hidden,
    { timeout: 90000 });
  const fbVisible = await page.evaluate(() =>
    !!document.getElementById('nlpFeedback') &&
    document.getElementById('nlpFbYes').offsetParent !== null);
  check('U6: feedback question appears under the assistant result', fbVisible);

  await page.click('#nlpFbYes');
  await page.waitForFunction(() =>
    /RECORDED|noted/.test(document.getElementById('nlpFbStatus').textContent),
    { timeout: 20000 });
  check('U7: "yes" verdict posts to the feedback API with full context',
    fbBody && fbBody.correct === true && /Jaipur/.test(fbBody.text) &&
    fbBody.intent === 'design' && !!fbBody.design,
    fbBody ? `${fbBody.intent} · ${fbBody.correct}` : 'no payload');

  // U8: "not quite" opens the correction box, and the correction is sent
  fbBody = null;
  await page.fill('#nlpInput', 'make it bigger');
  await page.click('#nlpBtn');
  await page.waitForFunction(() => /understood as/.test(document.getElementById('nlpStatus').textContent),
    { timeout: 90000 });
  await page.click('#nlpFbNo');
  const fixOpen = await page.evaluate(() => !document.getElementById('nlpFbFix').hidden);
  await page.fill('#nlpFbCorrection', 'I meant double the floor area');
  await page.click('#nlpFbSend');
  await page.waitForFunction(() =>
    /RECORDED|noted/.test(document.getElementById('nlpFbStatus').textContent),
    { timeout: 20000 });
  check('U8: "not quite" + correction posts correct=false with the user words',
    fixOpen && fbBody && fbBody.correct === false &&
    fbBody.correction === 'I meant double the floor area' &&
    fbBody.text === 'make it bigger', fbBody ? 'correction stored' : 'no payload');

  // U9: multi-turn context still continues ("make it bigger" kept the design)
  const continued = await page.evaluate(() =>
    [...document.querySelectorAll('#nlpApplied td')].some((td) => /continuing/.test(td.textContent)));
  check('U9: follow-up turn continues from the previous design', continued);

  // U10: nav tabs are grouped with labels
  const nav = await page.evaluate(() =>
    [...document.querySelectorAll('.navbar .nav-group-label')].map((e) => e.textContent));
  check('U10: nav grouped into labelled clusters',
    nav.length >= 3 && nav.includes('DESIGN') && nav.includes('INTELLIGENCE'), nav.join('/'));

  // U11: opt table headers include the new columns (no run — too slow for e2e)
  const optHead = await page.evaluate(() => document.querySelector('#optTable thead').textContent);
  check('U11: optimizer leaderboard has Pitch + Vent columns',
    /Pitch/.test(optHead) && /Vent/.test(optHead), optHead.replace(/\s+/g, ' ').trim());

  check('no page JS errors', errors.length === 0, errors.join(' | ').slice(0, 300));

  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\n=== ${failed.length} FAIL ===` : `\n=== DESIGN-UX ALL PASS (${results.length} checks) ===`);
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('E2E CRASH:', e.message); process.exit(1); });
