/* Screenshot the redesigned layout for visual review. */
const { chromium } = require('/tmp/pw/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({
    executablePath: '/home/user/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',
    args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  await page.goto('http://localhost:8010/dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const s = document.getElementById('location');
    return s && s.options.length >= 15;
  }, { timeout: 30000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: '/home/user/layout_top.png' });

  await page.evaluate(() => document.getElementById('sec3').scrollIntoView());
  await page.waitForTimeout(800);
  await page.screenshot({ path: '/home/user/layout_sec3.png' });

  // show the feedback widget live
  await page.fill('#nlpInput', 'a cool mud brick shelter for a family of six in Jaipur with a sloped roof');
  await page.click('#nlpBtn');
  await page.waitForFunction(() => !document.getElementById('nlpResult').hidden, { timeout: 90000 });
  await page.evaluate(() => document.getElementById('nlpFeedback').scrollIntoView({ block: 'center' }));
  await page.waitForTimeout(600);
  await page.screenshot({ path: '/home/user/layout_feedback.png' });

  // dark → light theme check on sec3 grouping
  await page.click('.theme-toggle');
  await page.evaluate(() => document.getElementById('sec3').scrollIntoView());
  await page.waitForTimeout(800);
  await page.screenshot({ path: '/home/user/layout_sec3_light.png' });

  await browser.close();
  console.log('done');
})().catch((e) => { console.error(e); process.exit(1); });
