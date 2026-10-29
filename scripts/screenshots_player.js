// Screenshots 25-33 (1.3.0 features: continue watching, library, playlists, player).
// Usage: node scripts/screenshots_player.js <base-url> <out-dir>
//   base-url: file:///.../preview/index.html  or  http://127.0.0.1:8787/ (mock server)
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
  page.on('pageerror', (e) => errors.push(String(e)));
  const go = async (hash, wait = 900) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
  const shot = async (name) => { await page.screenshot({ path: path.join(out, `${name}.jpg`), type: 'jpeg', quality: 82 }); console.log('shot', name); };
  const tabByText = async (t) => { await page.locator('button', { hasText: new RegExp(`^${t}$`) }).first().click(); await page.waitForTimeout(700); };

  await page.goto(base);
  await page.waitForTimeout(800);
  await page.click('.who button >> nth=0');
  await page.waitForTimeout(900);

  await go('#/foryou');
  await shot('25-continue-watching');

  await go('#/library');
  await shot('26-library');

  await tabByText('Playlists');
  await page.locator('a.pl-card').first().click();
  await page.waitForTimeout(900);
  await shot('27-playlist');

  // Player: play the playlist.
  await page.locator('.screen a.btn.primary').first().click();
  await page.waitForSelector('.player-wrap');
  await page.waitForTimeout(1500);
  const g = await page.locator('.gesture').boundingBox();
  const rx = g.x + g.width * 0.8, ry = g.y + g.height / 2;
  await page.mouse.click(rx, ry);
  await page.waitForTimeout(80);
  await page.mouse.click(rx, ry);
  await page.waitForTimeout(80);
  await page.mouse.click(rx, ry);
  await page.waitForTimeout(250);
  await shot('28-double-tap-skip');
  await page.waitForTimeout(1500);

  await page.mouse.click(g.x + g.width / 2, ry);  // show controls
  await page.waitForTimeout(400);
  const s = await page.locator('.scrub').boundingBox();
  await page.mouse.move(s.x + s.width * 0.2, s.y + s.height / 2);
  await page.mouse.down();
  await page.mouse.move(s.x + s.width * 0.62, s.y + s.height / 2, { steps: 8 });
  await page.waitForTimeout(200);
  await shot('29-scrubbing');
  await page.mouse.up();
  await page.waitForTimeout(600);

  await tabByText('Transcript');
  await page.waitForTimeout(600);
  const search = page.locator('section.transcript input').first();
  if (await search.count()) { await search.fill('the'); await page.waitForTimeout(500); }
  await page.locator('section.transcript').scrollIntoViewIfNeeded();
  await shot('30-transcript');

  // Play to the end of a shared video to show the end card with delete votes.
  await go('#/library/shared');
  await page.locator('.screen button.vitem').first().click();
  await page.waitForSelector('.player-wrap');
  await page.waitForTimeout(1200);
  await page.evaluate(() => { const m = document.querySelector('video, audio'); m.muted = true; m.currentTime = Math.max(0, m.duration - 0.3); return m.play().catch(() => {}); });
  await page.waitForTimeout(2500);
  await shot('31-delete-after-watching');

  await go('#/appearance');
  await tabByText('Light');
  await go('#/library', 300);
  await shot('32-light-mode');
  await go('#/appearance');
  await tabByText('Dark');

  await go('#/library');
  await page.locator('.screen button.vitem').first().click();
  await page.waitForSelector('.player-wrap');
  await page.setViewportSize({ width: 844, height: 390 });
  await page.waitForTimeout(1200);
  await shot('33-landscape');

  await browser.close();
  if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
  console.log('no page errors');
})();
