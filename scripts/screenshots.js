// Click through every screen at 390px and save screenshots.
// Usage: node scripts/screenshots.js <base-url> <out-dir>
//   base-url: http://127.0.0.1:8787/  (mock server)  or  file:///.../preview/index.html
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const base = process.argv[2] || 'http://127.0.0.1:8787/';
const out = process.argv[3] || 'screenshots';
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  const go = async (hash) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(700); };
  const shot = async (name, full = false) => { await page.screenshot({ path: path.join(out, `${name}.jpg`), fullPage: full, type: 'jpeg', quality: 82 }); console.log('shot', name); };

  await page.goto(base);
  await page.waitForTimeout(800);
  await shot('01-profiles');
  await page.click('.who button >> nth=0');
  await page.waitForTimeout(900);

  // Search + download
  await go('#/search');
  await page.fill('input[type=search]', 'sourdough bread');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.vitem .title');
  await page.waitForTimeout(500);
  await shot('02-search');
  await page.click('.vlist .vitem:not(:has(.badge)) >> nth=0');
  await page.waitForTimeout(500);
  await shot('03-download-sheet');
  await page.click('.sheet .btn.primary');
  await page.waitForTimeout(600);
  await page.click('.vlist .vitem:not(:has(.badge)) >> nth=1');
  await page.waitForTimeout(400);
  await page.click('.sheet .option >> text=Share with both of us');
  await page.click('.sheet .btn.primary');
  await page.waitForTimeout(1200);

  await go('#/downloads');
  await page.waitForTimeout(800);
  await shot('04-downloads-progress');
  await page.waitForTimeout(9000);
  await go('#/queue');
  await page.waitForTimeout(800);
  await shot('05-queue-mine');
  await page.click('.seg button >> text=Shared');
  await page.waitForTimeout(700);
  await shot('06-queue-shared');
  await page.click('.seg button >> text=My queue');
  await page.waitForTimeout(500);

  // Player
  await page.click('.hitem .vitem >> nth=0');
  await page.waitForTimeout(1500);
  await page.evaluate(async () => { const v = document.querySelector('video'); v.muted = true; try { await v.play(); } catch {} });
  await page.waitForTimeout(2500);
  await page.evaluate(() => document.querySelector('video').pause());
  await page.waitForTimeout(400);
  await shot('07-player');
  await go('#/downloads');
  await page.waitForTimeout(1200);
  await shot('08-downloads-library', true);

  // For you
  await go('#/foryou');
  await page.waitForTimeout(1500);
  if (await page.$('.empty .btn')) { await page.click('.empty .btn'); await page.waitForTimeout(2500); }
  await shot('09-foryou');
  await page.click('.reason >> nth=0');
  await page.waitForTimeout(900);
  await shot('10-why');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  await page.click('.seg button >> text=Both of us');
  await page.waitForTimeout(1000);
  await shot('11-foryou-both');

  // Shorts
  await go('#/shorts');
  await page.waitForTimeout(2500);
  await shot('12-shorts');

  // Settings, appearance, algorithm
  await go('#/settings');
  await page.waitForTimeout(1000);
  await shot('13-settings', true);
  await go('#/appearance');
  await page.waitForTimeout(800);
  await shot('14-appearance', true);
  await page.click('.preset >> nth=2'); // Daylight
  await page.waitForTimeout(800);
  await shot('15-appearance-daylight');
  await page.click('.preset >> nth=0');
  await page.waitForTimeout(500);
  await go('#/algorithm');
  await page.waitForTimeout(900);
  await shot('16-algorithm', true);
  await page.click('.seg button >> text=History');
  await page.waitForTimeout(500);
  await shot('17-algorithm-history');

  // Music
  await go('#/music');
  await page.waitForTimeout(900);
  await shot('19-music-library');
  await page.fill('input[type=search]', 'theo lark');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.track .title');
  await page.waitForTimeout(400);
  await page.click('button[aria-label^="Download"] >> nth=0');
  await page.waitForTimeout(500);
  await shot('20-music-download-sheet');
  await page.click('.sheet .btn.primary');
  await page.waitForTimeout(2500);
  await shot('21-music-search');
  await page.waitForTimeout(16000);
  await page.click('.track-main >> nth=0');
  await page.waitForTimeout(1500);
  await shot('22-music-playing');
  await go('#/queue');
  await page.waitForTimeout(800);
  await shot('23-mini-player-other-tab');

  await go('#/shortcut');
  await page.waitForTimeout(700);
  await shot('24-share-shortcut', true);

  // Second profile has its own theme
  await go('#/profiles');
  await page.waitForTimeout(700);
  await page.click('.who button >> nth=1');
  await page.waitForTimeout(900);
  await shot('18-profile2-foryou');

  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no console errors');
  await browser.close();
})();
