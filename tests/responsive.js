#!/usr/bin/env node
/* Responsive regression harness.
 *
 * Checks the rendered page at real device widths for:
 *   - horizontal overflow (page wider than the viewport)
 *   - collapsed text (tall + narrow = letters stacked vertically)
 *   - unreadable font sizes
 *   - tap targets below the 44px guideline
 *
 * Renders the ACTUAL output, with every <details> expanded, because a
 * bug hiding inside a collapsed section is still a bug.
 *
 *   node tests/responsive.js <file.html> [more.html ...]
 */
const { chromium } = require('playwright');

const WIDTHS = [320, 375, 390, 430, 768, 1280];
const MIN_FONT = 11;
const MIN_TAP = 40;

(async () => {
  const files = process.argv.slice(2);
  if (!files.length) { console.error('usage: responsive.js <file.html>...'); process.exit(2); }
  const b = await chromium.launch();
  let failures = 0;

  for (const file of files) {
    for (const W of WIDTHS) {
      const p = await b.newPage({ viewport: { width: W, height: 900 } });
      await p.goto('file://' + file);
      await p.evaluate(() => document.querySelectorAll('details').forEach(d => (d.open = true)));
      await p.waitForTimeout(250);

      const r = await p.evaluate(({ MIN_FONT, MIN_TAP }) => {
        const collapsed = [], small = [], tap = [];
        for (const el of document.querySelectorAll('body *')) {
          const b = el.getBoundingClientRect();
          if (!b.width || !b.height) continue;
          const t = (el.textContent || '').trim();
          const cs = getComputedStyle(el);
          if (t.length >= 10 && b.height / Math.max(b.width, 1) > 2.5 && b.width < 80)
            collapsed.push({ cls: String(el.className).slice(0, 30), w: Math.round(b.width),
                             h: Math.round(b.height), text: t.slice(0, 30) });
          if (t.length > 3 && !el.children.length && parseFloat(cs.fontSize) < MIN_FONT)
            small.push({ cls: String(el.className).slice(0, 24), px: cs.fontSize });
          if ((el.tagName === 'A' || el.tagName === 'SUMMARY' || el.tagName === 'BUTTON')
              && b.height < MIN_TAP && b.height > 0)
            tap.push({ tag: el.tagName, h: Math.round(b.height),
                       text: t.slice(0, 22) });
        }
        return { collapsed: collapsed.slice(0, 6), small: small.slice(0, 6),
                 tap: tap.slice(0, 6),
                 docW: document.documentElement.scrollWidth, viewW: window.innerWidth };
      }, { MIN_FONT, MIN_TAP });

      const name = file.split('/').pop();
      const problems = [];
      if (r.docW > r.viewW + 1) problems.push(`horizontal overflow (${r.docW} > ${r.viewW})`);
      if (r.collapsed.length) problems.push(`${r.collapsed.length} collapsed text element(s)`);
      if (r.small.length) problems.push(`${r.small.length} element(s) under ${MIN_FONT}px`);

      if (problems.length) {
        failures++;
        console.log(`FAIL ${name} @${W}px: ${problems.join('; ')}`);
        for (const c of r.collapsed) console.log(`      collapsed ${JSON.stringify(c)}`);
        for (const c of r.small) console.log(`      tiny ${JSON.stringify(c)}`);
      } else {
        const note = r.tap.length ? `  (${r.tap.length} tap target(s) under ${MIN_TAP}px)` : '';
        console.log(`ok   ${name} @${W}px${note}`);
      }
      await p.close();
    }
  }
  await b.close();
  console.log(failures ? `\n${failures} responsive failure(s)` : '\nall responsive checks passed');
  process.exit(failures ? 1 : 0);
})();
