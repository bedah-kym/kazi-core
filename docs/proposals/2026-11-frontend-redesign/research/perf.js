const { chromium } = require('playwright');
const B = 'http://127.0.0.1:8765';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto(B + '/accounts/login/');
  await page.fill('#username', 'demo'); await page.fill('#password', 'demo-pass-123');
  await Promise.all([page.waitForNavigation().catch(() => {}), page.click('form button[type=submit]')]);
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable'); await cdp.send('Performance.enable');
  for (const [name, path] of [['chat', '/chatbot/redirect/'], ['dashboard', '/accounts/dashboard/'], ['settings', '/accounts/settings/']]) {
    const reqs = new Map(); let bytes = {};
    const onResp = e => reqs.set(e.requestId, { url: e.response.url, type: e.type });
    const onFin = e => { const r = reqs.get(e.requestId); if (r) r.size = e.encodedDataLength; };
    cdp.on('Network.responseReceived', onResp); cdp.on('Network.loadingFinished', onFin);
    await cdp.send('Network.clearBrowserCache');
    await page.goto(B + path, { waitUntil: 'load' }); await page.waitForTimeout(2500);
    cdp.off('Network.responseReceived', onResp); cdp.off('Network.loadingFinished', onFin);
    const all = [...reqs.values()].filter(r => r.size != null);
    const third = all.filter(r => !r.url.startsWith(B));
    const sum = (a, t) => Math.round(a.filter(r => !t || r.type === t).reduce((s, r) => s + r.size, 0) / 1024);
    const m = (await cdp.send('Performance.getMetrics')).metrics.reduce((o, x) => (o[x.name] = x.value, o), {});
    const dom = await page.evaluate(() => document.querySelectorAll('*').length);
    console.log(JSON.stringify({ page: name, requests: all.length, thirdPartyRequests: third.length, thirdPartyHosts: [...new Set(third.map(r => new URL(r.url).host))],
      totalKB: sum(all), thirdPartyKB: sum(third), scriptKB: sum(all, 'Script'), cssKB: sum(all, 'Stylesheet'), fontKB: sum(all, 'Font'),
      jsHeapMB: +(m.JSHeapUsedSize / 1048576).toFixed(1), domNodes: dom, scriptDurMs: Math.round(m.ScriptDuration * 1000) }));
  }
  await browser.close();
})();
