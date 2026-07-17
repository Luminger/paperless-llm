// Visual-inspection harness: screenshots app pages in light/dark.
// Usage: node scripts/shoot.mjs [baseUrl] [outDir] [path...]
import { mkdirSync } from "node:fs";
import puppeteer from "puppeteer-core";

const base = process.argv[2] ?? "http://localhost:5173";
const out = process.argv[3] ?? "/tmp/pllm-shots";
const paths = process.argv.slice(4);
const routes = paths.length
  ? paths
  : ["/", "/documents", "/taxonomy", "/jobs", "/log"];

mkdirSync(out, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: "/etc/profiles/per-user/simon/bin/chromium",
  headless: "new",
  args: ["--no-sandbox", "--window-size=1440,900"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });

for (const scheme of ["light", "dark"]) {
  await page.emulateMediaFeatures([
    { name: "prefers-color-scheme", value: scheme },
  ]);
  for (const r of routes) {
    await page.goto(base + r, { waitUntil: "networkidle0", timeout: 30000 });
    await new Promise((res) => setTimeout(res, 400));
    const name =
      (r === "/" ? "home" : r.replaceAll("/", "_").replace(/^_/, "")) +
      `-${scheme}.png`;
    await page.screenshot({ path: `${out}/${name}` });
    console.log(name);
  }
}
await browser.close();
