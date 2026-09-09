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
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/404|favicon/.test(m.text())) errors.push(m.text()); });
  const BASE = 'http://localhost:8010';

  const presetState = () => page.evaluate(() => {
    const hd = [...document.querySelectorAll('#presetGrid .preset-group-hd')].map(e => e.textContent);
    const cards = [];
    let group = null;
    for (const el of document.querySelectorAll('#presetGrid > *')) {
      if (el.classList.contains('preset-group-hd')) group = el.textContent;
      else if (el.classList.contains('preset-card'))
        cards.push({ group, name: el.querySelector('.preset-card-hd b').textContent,
                     chip: [...el.querySelectorAll('.chips i')].map(c => c.textContent) });
    }
    return { hd, cards,
             siteTag: document.getElementById('presetSiteTag').textContent,
             chip: document.getElementById('structSource').textContent };
  });
  const setLoc = (namePart) => page.evaluate((np) => {
    const sel = document.getElementById('location');
    const opt = [...sel.options].find(o => o.textContent.startsWith(np));
    if (!opt) return null;
    sel.value = opt.value; sel.dispatchEvent(new Event('change'));
    return opt.value;
  }, namePart);

  await page.goto(BASE + '/dashboard.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  // ---- A. default = Prayagraj with grouped presets ----
  const opts = await page.evaluate(() => [...document.querySelectorAll('#location option')].map(o => o.textContent));
  const uniq = [...new Set(opts)];
  const selVal = await page.evaluate(() => document.getElementById('location').value);
  const selText = await page.evaluate(() => document.getElementById('location').selectedOptions[0]?.textContent || '');
  check('A0: dropdown clean + default Prayagraj', opts.length === 15 && uniq.length === 15 && uniq.some(t => /^Leh/.test(t)) && /^Prayagraj/.test(selText),
        'opts=' + opts.length + ' selected=' + selText + ' val=' + selVal);
  let st = await presetState();
  check('A1: default site Prayagraj · composite', /SITE PRAYAGRAJ/.test(st.siteTag), st.siteTag);
  check('A2: group headers rec/other', st.hd.length >= 2 && /RECOMMENDED · COMPOSITE/.test(st.hd[0]) && /OTHER CLIMATE ZONES/.test(st.hd[1]), JSON.stringify(st.hd));
  const recPry = st.cards.filter(c => c.group.startsWith('RECOMMENDED'));
  check('A3: recommended composite list, brick first, trio prefix stable', recPry.length === 6 && recPry[0].name === 'Composite Brick Studio' && recPry[1].name === 'Rapid Relief Kit' && recPry[2].name === 'Rural Low-Cost Unit' && !recPry.some(c => /Ladakh|Kashmir|AAC|Coastal|Desert|Temperate/i.test(c.name)), JSON.stringify(recPry.map(c => c.name)));
  const refPry = st.cards.filter(c => c.group.startsWith('OTHER'));
  check('A4: Leh in reference group only', refPry.some(c => /Ladakh/i.test(c.name)), JSON.stringify(refPry.map(c => c.name)));

  // ---- B. Leh -> load preset -> Prayagraj auto-swap ----
  await setLoc('Leh');
  await page.waitForFunction(() => {
    const hd = [...document.querySelectorAll('#presetGrid .preset-group-hd')].map(e => e.textContent);
    return hd.some(t => /^RECOMMENDED · COLD/.test(t));
  }, { timeout: 25000 });
  const loadedName = await page.evaluate(() => {
    let first = null, inRec = false;
    for (const el of document.querySelectorAll('#presetGrid > *')) {
      if (el.classList.contains('preset-group-hd')) inRec = /^RECOMMENDED/.test(el.textContent);
      else if (inRec && el.classList.contains('preset-card') && !first) first = el;
    }
    if (first) first.querySelector('button[data-preset]').click();
    return first ? first.querySelector('.preset-card-hd b').textContent : null;
  });
  await page.waitForTimeout(4000);
  st = await presetState();
  check('B1: Leh preset in studio at Leh', /^PRESET · LADAKH/.test(st.chip), st.chip + ' (' + loadedName + ')');
  const remembered = await page.evaluate(() => localStorage.getItem('shl-site'));
  check('B2: choice remembered', remembered === 'loc_34.1526_77.5771', remembered);

  await setLoc('Prayagraj');
  try {
    await page.waitForFunction(() => /PRESET · PRAYAGRAJ_BRICK/.test(document.getElementById('structSource').textContent), { timeout: 30000 });
  } catch (e) {}
  await page.waitForTimeout(2000);
  st = await presetState();
  check('B3: studio auto-swapped to Prayagraj preset', st.chip === 'PRESET · PRAYAGRAJ_BRICK', st.chip);

  // ---- C. reload keeps choice ----
  await setLoc('Leh');
  await page.waitForFunction(() => {
    const hd = [...document.querySelectorAll('#presetGrid .preset-group-hd')].map(e => e.textContent);
    return hd.some(t => /^RECOMMENDED · COLD/.test(t));
  }, { timeout: 25000 });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  const afterReload = await page.evaluate(() => ({
    val: document.getElementById('location').value,
    siteTag: document.getElementById('presetSiteTag').textContent,
  }));
  check('C1: reload restores Leh', afterReload.val === 'loc_34.1526_77.5771' && /SITE LEH/.test(afterReload.siteTag), JSON.stringify(afterReload));
  await page.evaluate(() => { try { localStorage.removeItem('shl-site'); } catch (e) {} });

  check('no page JS errors', errors.length === 0, errors.join(' | ').slice(0, 200));
  const failed = results.filter(r => !r.ok);
  console.log('=== ' + (failed.length === 0 ? 'ALL PASS' : failed.length + ' FAILED') + ' (' + results.length + ' checks) ===');
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error('CRASH:', e.message); process.exit(1); });
