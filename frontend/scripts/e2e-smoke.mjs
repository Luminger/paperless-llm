// End-to-end smoke suite: walks every surface of a RUNNING instance
// (default: the playground on :8100) and fails on console errors,
// failed requests, or missing key elements.
//
//   node scripts/e2e-smoke.mjs [base-url]
//
// Uses the system chromium via puppeteer-core — no browser downloads.

import puppeteer from "puppeteer-core";

const BASE = process.argv[2] ?? "http://127.0.0.1:8100";
const CHROMIUM =
  process.env.CHROMIUM_BIN ?? "/etc/profiles/per-user/simon/bin/chromium";

const failures = [];
const check = (ok, msg) => {
  if (ok) console.log(`  ok: ${msg}`);
  else {
    console.error(`  FAIL: ${msg}`);
    failures.push(msg);
  }
};

const browser = await puppeteer.launch({
  executablePath: CHROMIUM,
  headless: "new",
  args: ["--no-sandbox"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });

const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("requestfailed", (r) => {
  // EventSource teardown on navigation is expected noise.
  if (!r.url().includes("/events")) {
    consoleErrors.push(`request failed: ${r.url()} (${r.failure()?.errorText})`);
  }
});

async function visit(path, expectText) {
  await page.goto(`${BASE}${path}`, { waitUntil: "load" });
  await new Promise((r) => setTimeout(r, 1200));
  const body = await page.evaluate(() => document.body.innerText);
  check(body.includes(expectText), `${path} shows "${expectText}"`);
}

console.log(`smoke against ${BASE}`);

// Every top-level surface renders its key content.
await visit("/", "Dashboard");
await visit("/documents", "Documents");
await visit("/taxonomy", "Taxonomy");
await visit("/jobs", "Jobs");
await visit("/log", "Log");

// Deep links: settings modal with fragment section.
await visit("/settings#prompts", "Agent system prompt");
check(
  await page.evaluate(() => location.hash === "#prompts"),
  "settings fragment survives",
);

// Documents: URL-backed filter state.
await page.goto(`${BASE}/documents?page=1`, { waitUntil: "load" });
await new Promise((r) => setTimeout(r, 1200));
const rows = await page.$$eval("ul.divide-y > li", (t) => t.length);
check(rows > 0, `documents list has rows (${rows})`);

// A session detail (if any session exists).
const sessions = await page.evaluate(async () => {
  const r = await fetch("/api/sessions?page_size=1");
  return (await r.json()).results;
});
if (sessions.length > 0) {
  await visit(`/sessions/${sessions[0].id}`, sessions[0].title.slice(0, 12));
}

// The user menu opens and offers Settings + theme.
await page.goto(`${BASE}/`, { waitUntil: "load" });
await new Promise((r) => setTimeout(r, 800));
await page.click('button[aria-label="user menu"]');
await new Promise((r) => setTimeout(r, 400));
const menuText = await page.evaluate(() => document.body.innerText);
check(menuText.includes("Settings"), "user menu opens with Settings");
check(menuText.includes("Theme"), "user menu offers theme switch");

// No console errors anywhere on the walk.
check(
  consoleErrors.length === 0,
  `no console errors (${consoleErrors.length ? consoleErrors.join(" | ").slice(0, 300) : "clean"})`,
);

await browser.close();
if (failures.length) {
  console.error(`\n${failures.length} smoke failure(s)`);
  process.exit(1);
}
console.log("\nsmoke suite passed");
