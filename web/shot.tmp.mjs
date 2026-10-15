import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
await page.goto("http://127.0.0.1:9119/");
await page.waitForTimeout(2500);
await page.screenshot({ path: process.argv[2] });
// open a claim detail: click the first pending claim card
const cards = page.locator(".claim-card");
if (await cards.count() > 0) {
  await cards.nth(5).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: process.argv[3] });
}
await browser.close();
