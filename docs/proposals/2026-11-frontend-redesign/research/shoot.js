const { chromium } = require('playwright');
const B = 'http://127.0.0.1:8765';
const out = process.argv[2];
const VPS = { phone: [390, 844], tablet: [820, 1180], desktop: [1440, 900] };
const PUBLIC = { landing: '/', login: '/accounts/login/', register: '/accounts/register/', pricing: '/accounts/pricing/' };
// Needs a user whose workspace has onboarding_completed=True, or most of these redirect.
const AUTHED = { dashboard: '/accounts/dashboard/', chat: '/chatbot/redirect/', settings: '/accounts/settings/', reminders: '/accounts/reminders/',
  personas: '/accounts/personas/', skills: '/accounts/skills/', workflows: '/workflows/', inbox: '/workflows/inbox/', wallet: '/payments/wallet/',
  invoice: '/payments/invoice/create/', transactions: '/payments/transactions/', travel: '/travel/plan/', itineraries: '/travel/itineraries/',
  notifications: '/notifications/', onboarding: '/accounts/onboarding/', why: '/accounts/why/' };
(async () => {
  const browser = await chromium.launch();
  const rows = [];
  for (const [vp, [w, h]] of Object.entries(VPS)) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, hasTouch: vp !== 'desktop', isMobile: vp === 'phone' });
    const page = await ctx.newPage();
    const failed = new Set();
    page.on('requestfailed', r => failed.add(new URL(r.url()).host));
    const visit = async (name, path) => {
      try {
        const resp = await page.goto(B + path, { waitUntil: 'load', timeout: 25000 });
        await page.waitForTimeout(1200);
        const m = await page.evaluate(() => {
          const de = document.documentElement;
          const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
          const targets = [...document.querySelectorAll('a,button,input:not([type=hidden]),select,textarea,[role=button]')].filter(vis);
          const small = targets.filter(e => { const r = e.getBoundingClientRect(); return r.width < 40 || r.height < 40; }).length;
          const wide = [...document.querySelectorAll('body *')].filter(e => vis(e) && e.getBoundingClientRect().right > innerWidth + 1).slice(0, 4)
            .map(e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
          const tiny = [...document.querySelectorAll('body *')].filter(e => vis(e) && e.childElementCount === 0 && e.textContent.trim() && parseFloat(getComputedStyle(e).fontSize) < 12).length;
          const inputs16 = [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,select')].filter(vis).filter(e => parseFloat(getComputedStyle(e).fontSize) < 16).length;
          return { overflowX: de.scrollWidth - innerWidth, targets: targets.length, small, wide, tiny, inputs16, pageH: de.scrollHeight, fonts: [...new Set([...document.querySelectorAll('body,h1,h2,button,input')].map(e => getComputedStyle(e).fontFamily.split(',')[0].trim()))] };
        });
        await page.screenshot({ path: `${out}/${name}-${vp}.png`, fullPage: false });
        rows.push({ page: name, vp, status: resp.status(), url: new URL(page.url()).pathname, ...m });
      } catch (e) { rows.push({ page: name, vp, error: String(e).slice(0, 120) }); }
    };
    for (const [n, p] of Object.entries(PUBLIC)) await visit(n, p);
    await page.goto(B + '/accounts/login/');
    await page.fill('#username', 'demo'); await page.fill('#password', 'demo-pass-123');
    await Promise.all([page.waitForNavigation({ timeout: 20000 }).catch(() => {}), page.click('form button[type=submit], form button')]);
    rows.push({ page: '_afterLogin', vp, url: new URL(page.url()).pathname });
    for (const [n, p] of Object.entries(AUTHED)) await visit(n, p);
    rows.push({ page: '_failedHosts', vp, hosts: [...failed] });
    await ctx.close();
  }
  await browser.close();
  console.log(rows.map(r => JSON.stringify(r)).join('\n'));
})();
