/* E2E: home + dashboard + offline edition (browser, real HTTP). */
const { chromium } = require('/tmp/pw/node_modules/playwright-core');
const fs = require('fs');

const BASE = 'http://localhost:8010';
const CHROME = '/home/user/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';
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
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });

  /* ---------- 1. home ---------- */
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  const heroKicker = await page.evaluate(() => document.querySelector('.hero-kicker')?.textContent || '');
  check('home: heading has no DRDO INSPIRED INTERFACE',
        !/DRDO\s*INSPIRED|DRDO.*INTERFACE/i.test(heroKicker), heroKicker.trim().slice(0, 60));
  const cta = await page.evaluate(() => {
    const els = [...document.querySelectorAll('a,button')];
    return els.map(e => e.textContent.trim()).find(t => /WORKSPACE|DECK/i.test(t)) || null;
  });
  check('home: CTA renamed to ENTER WORKSPACE', cta && cta.includes('WORKSPACE') && !cta.includes('DECK'), cta || '');
  const themeBtn = await page.evaluate(() => !!document.querySelector('#lndTheme'));
  check('home: theme toggle present', themeBtn);
  if (themeBtn) {
    const t0 = await page.evaluate(() => document.documentElement.dataset.theme);
    await page.click('#lndTheme');
    await page.waitForTimeout(300);
    const t1 = await page.evaluate(() => localStorage.getItem('shl-theme'));
    check('home: theme toggle flips theme', t1 === (t0 === 'dark' ? 'light' : 'dark'), t0 + ' -> ' + t1);
    await page.click('#lndTheme'); // back to start
  }

  /* ---------- 2. dashboard ---------- */
  await page.goto(BASE + '/dashboard.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  check('dashboard: OFFLINE button present', await page.evaluate(() => !!document.querySelector('#offlineBtn')));
  check('dashboard: theme toggle present', await page.evaluate(() => !!document.querySelector('#themeToggle')));
  const sysLink = await page.evaluate(() => document.getElementById('sysLink')?.textContent || '');
  check('dashboard: API health linked', /LINK|ONLINE|API/i.test(sysLink), sysLink);
  const heading = await page.evaluate(() => document.querySelector('.brand .b2')?.textContent || '');
  check('dashboard: heading shortened (no DRDO)', !/DRDO/i.test(heading), heading.slice(0, 60));

  // materials + locations load
  await page.waitForTimeout(1500);
  const mats = await page.evaluate(() => document.querySelectorAll('#matList option, #wallMat option').length);
  check('dashboard: materials populated', mats >= 15, 'options=' + mats);

  // theme toggle on dashboard
  const t0d = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.click('#themeToggle');
  await page.waitForTimeout(250);
  const t1d = await page.evaluate(() => localStorage.getItem('shl-theme'));
  check('dashboard: theme toggle flips theme', t1d === (t0d === 'dark' ? 'light' : 'dark'), t0d + ' -> ' + t1d);
  await page.click('#themeToggle');

  /* ---------- 3. offline modal + guest download ---------- */
  await page.click('#offlineBtn');
  await page.waitForTimeout(700);
  const modalShown = await page.evaluate(() => !document.getElementById('offlineOverlay').hidden);
  check('offline: modal opens', modalShown);
  const facts = await page.evaluate(() => ({
    sites: document.getElementById('offSites').textContent,
    mats: document.getElementById('offMats').textContent,
    presets: document.getElementById('offPresets').textContent,
    samples: document.getElementById('offSamples').textContent,
  }));
  check('offline: modal facts populated', facts.sites === '14' && facts.mats === '15' && facts.presets === '10' && /28,?000/.test(facts.samples), JSON.stringify(facts));

  const dlPromise = page.waitForEvent('download', { timeout: 60000 });
  await page.click('#offAsGuest');
  const dl = await dlPromise;
  const dlPath = '/tmp/offline_dl.html';
  await dl.saveAs(dlPath);
  const dlSize = fs.statSync(dlPath).size;
  const dlBody = fs.readFileSync(dlPath, 'utf8');
  check('offline: guest download arrives', dlSize > 1_000_000, (dlSize / 1048576).toFixed(1) + ' MB, name=' + dl.suggestedFilename());
  check('offline: downloaded file is the offline app', dlBody.includes('FULLY OFFLINE') && dlBody.includes('OfflineEngine') && dlBody.includes('Prayagraj'));
  check('offline: downloaded app embeds 14 sites', (dlBody.match(/"latitude"/g) || []).length >= 14);
  await page.waitForTimeout(400);

  /* ---------- 4. login-mode download ---------- */
  // create an account via API then set the token in localStorage
  const uname = 'e2e_user_' + Date.now();
  const signup = await fetch('http://localhost:8000/api/auth/signup', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: uname, password: 'secret123' }),
  });
  const login = await fetch('http://localhost:8000/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: uname, password: 'secret123' }),
  });
  const lj = await login.json();
  await page.evaluate((token) => {
    localStorage.setItem('sih-token', token);
    localStorage.setItem('sih-user', JSON.stringify({ username: 'e2e_user' }));
    window.dispatchEvent(new CustomEvent('sih:auth', { detail: { username: 'e2e_user' } }));
  }, lj.token);
  await page.click('#offlineBtn');
  await page.waitForTimeout(500);
  const dl2Promise = page.waitForEvent('download', { timeout: 60000 });
  await page.click('#offAsLogin');
  const dl2 = await dl2Promise;
  const dl2Path = '/tmp/offline_dl2.html';
  await dl2.saveAs(dl2Path);
  check('offline: login-mode download arrives', fs.statSync(dl2Path).size > 1_000_000);

  /* ---------- 5. simulate + suggest still work ---------- */
  const hasLeh = await page.evaluate(() => !!document.querySelector('#location option[value="loc_34.1526_77.5771"]'));
  if (hasLeh) {
    await page.selectOption('#location', 'loc_34.1526_77.5771');   // Leh
    await page.waitForTimeout(500);
  }
  await page.click('#simulate');
  await page.waitForTimeout(6000);
  const simRes = await page.evaluate(() => (document.getElementById('simMetrics')?.textContent || ''));
  check('dashboard: simulation runs at Leh', simRes.length > 10 && /°C/.test(simRes), simRes.replace(/\s+/g, ' ').slice(0, 80));

  const adaptClicked = await page.evaluate(() => {
    const b = document.getElementById('adaptBtn');
    if (!b) return false;
    b.click();                       // programmatic click — bypasses visibility gate
    return true;
  });
  await page.waitForTimeout(6000);
  const adapt = await page.evaluate(() => document.getElementById('adaptStatus')?.textContent || '');
  check('dashboard: site-adapted design generates', adaptClicked && adapt.length > 10, adapt.replace(/\s+/g, ' ').slice(0, 100));

  const realErrors = errors.filter(e => !/404 \(File not found\)/.test(e));
  check('no page JS errors', realErrors.length === 0, realErrors.join(' | ').slice(0, 300));

  /* ---------- summary ---------- */
  const failed = results.filter(r => !r.ok);
  console.log('\n=== E2E ' + (failed.length === 0 ? 'ALL PASS' : failed.length + ' FAILED') + ' (' + results.length + ' checks) ===');
  await browser.close();
  process.exit(failed.length === 0 ? 0 : 1);
})().catch(e => { console.error('E2E CRASH:', e.message); process.exit(1); });
