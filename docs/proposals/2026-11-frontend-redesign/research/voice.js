const { chromium } = require('playwright');
const B = 'http://127.0.0.1:8765', out = process.argv[2];
async function login(page) {
  await page.goto(B + '/accounts/login/');
  await page.fill('#username', 'demo'); await page.fill('#password', 'demo-pass-123');
  await Promise.all([page.waitForNavigation().catch(() => {}), page.click('form button[type=submit]')]);
  await page.goto(B + '/chatbot/redirect/', { waitUntil: 'load' }); await page.waitForTimeout(2500);
}
(async () => {
  const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
  for (const [vp, w, h] of [['phone', 390, 844], ['desktop', 1440, 900]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, permissions: ['microphone'], hasTouch: vp === 'phone', isMobile: vp === 'phone' });
    await ctx.addInitScript(() => { try { localStorage.setItem('kazi_tour_seen', 'true'); } catch (e) {} }); const page = await ctx.newPage(); const errs = [];
    page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });
    await login(page);
    const box = async sel => { const b = await page.locator(sel).boundingBox(); return b ? `${Math.round(b.width)}x${Math.round(b.height)}` : 'hidden'; };
    const r = { vp, micSize: await box('#voice-record-btn'), micLabelInMarkup: await page.locator('#voice-record-btn').getAttribute('aria-label'), micTitle: await page.locator('#voice-record-btn').getAttribute('title') };
    let t = Date.now(); await page.click('#voice-record-btn');
    await page.waitForFunction(() => getComputedStyle(document.getElementById('voice-recording-overlay')).display !== 'none', null, { timeout: 5000 });
    r.clickToOverlayMs = Date.now() - t;
    r.cancelSize = await box('#cancel-recording'); r.cancelLabel = await page.locator('#cancel-recording').getAttribute('aria-label');
    r.stopSize = await box('#voice-record-btn');
    r.textInputDisabledWhileRecording = await page.locator('#chat-message-input').isDisabled();
    r.overlayText = (await page.locator('#voice-recording-overlay').innerText()).replace(/\s+/g, ' ').slice(0, 80);
    r.liveRegion = await page.evaluate(() => !!document.querySelector('#voice-recording-overlay[aria-live], #voice-recording-overlay [aria-live], #voice-recording-overlay[role=status]'));
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${out}/voice-recording-${vp}.png` });
    const before = await page.locator('.voice-bubble').count();
    r.stopButtonReachable = await page.evaluate(() => { const b = document.getElementById('voice-record-btn').getBoundingClientRect(); const el = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return !!(el && el.closest('#voice-record-btn')); });
    r.cancelReachable = await page.evaluate(() => { const b = document.getElementById('cancel-recording').getBoundingClientRect(); const el = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return !!(el && el.closest('#cancel-recording')); });
    t = Date.now(); await page.evaluate(() => document.getElementById('voice-record-btn').click());
    try { await page.waitForFunction(n => document.querySelectorAll('.voice-bubble').length > n, before, { timeout: 15000 }); r.stopToBubbleMs = Date.now() - t; } catch { r.stopToBubbleMs = 'no bubble within 15s'; }
    r.anySendingIndicator = await page.evaluate(() => !!document.querySelector('.upload-progress-bar:not([style*="none"]), .voice-sending, .voice-uploading'));
    await page.waitForTimeout(8000);
    r.transcriptAfter8s = await page.locator('.voice-transcript').last().innerText().catch(() => 'n/a');
    r.aiRepliedToVoice = await page.evaluate(() => { const li = [...document.querySelectorAll('[id^="messages-room-"] li')]; const i = li.findIndex(e => e.querySelector('.voice-bubble')); return i >= 0 && li.slice(i + 1).some(e => e.querySelector('.kazi-message')); });
    r.playButton = await box('.voice-bubble button, .voice-bubble [id^="play-"]');
    r.consoleErrors = errs.slice(0, 3);
    await page.screenshot({ path: `${out}/voice-sent-${vp}.png` });
    console.log(JSON.stringify(r)); await ctx.close();
  }
  // denied permission
  const b2 = await chromium.launch({ args: ['--use-fake-device-for-media-stream', '--deny-permission-prompts'] });
  const c2 = await b2.newContext({ viewport: { width: 390, height: 844 } }); await c2.addInitScript(() => { try { localStorage.setItem('kazi_tour_seen', 'true'); } catch (e) {} }); const p2 = await c2.newPage();
  let dlg = null; p2.on('dialog', async d => { dlg = `${d.type()}: ${d.message()}`; await d.dismiss(); });
  await login(p2); await p2.click('#voice-record-btn'); await p2.waitForTimeout(2000);
  console.log(JSON.stringify({ denied: dlg, overlayShown: await p2.evaluate(() => getComputedStyle(document.getElementById('voice-recording-overlay')).display !== 'none') }));
  await b2.close(); await browser.close();
})();
