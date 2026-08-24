/**
 * Dev-only rendered-UI check.
 *
 * Drives an already-installed Chrome or Edge over the DevTools protocol to
 * screenshot every SwitchOps view at several widths and report console errors,
 * failed requests and horizontal overflow. Compilation passing is not evidence
 * that the UI looks right, so this closes that gap without adding a browser
 * automation dependency to the product.
 *
 * Usage (frontend static export + backend must already be running):
 *   node scripts/visual-check.mjs --url http://localhost:3000 --out .visual
 *
 * Nothing here ships in the desktop app or the Python sidecar.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, token, index, all) => {
    if (token.startsWith("--")) pairs.push([token.slice(2), all[index + 1]]);
    return pairs;
  }, []),
);

const BASE_URL = args.url || "http://localhost:3000";
const OUT_DIR = args.out || ".visual";
const PORT = Number(args.port || 9222);

const BROWSERS = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];

const VIEWPORTS = [
  { name: "desktop", width: 1680, height: 1050 },
  { name: "laptop", width: 1366, height: 900 },
  { name: "narrow", width: 900, height: 1100 },
];

/** Tab label -> screenshot slug. Labels match the nav buttons. */
const VIEWS = [
  { label: "EWPS Observatory", slug: "ewps-observatory" },
  { label: "Overview", slug: "overview" },
  { label: "Lab Assurance", slug: "lab-assurance" },
  {
    label: "Lab Assurance",
    slug: "lab-assurance-capabilities",
    prepare: `[...document.querySelectorAll(".assurance-tabs button")]
      .find((button) => button.textContent.trim() === "Capabilities")?.click(), true`,
    settle: 500,
  },
  {
    label: "Visual network",
    slug: "network",
    // Keep a selected physical interface for the later Change Control view so
    // the assurance planner itself, not only its empty state, is rendered.
    prepare: `document.querySelector("[data-port]")?.click(), true`,
    settle: 500,
  },
  { label: "What changed", slug: "events" },
  { label: "Command guide", slug: "guide" },
  {
    label: "Change control",
    slug: "change-control",
    // Exercise the dry-run planner so its output is inspected too. This is a
    // read-only allowlisted operation.
    prepare: `(() => {
      [...document.querySelectorAll("button")]
        .find((button) => /review disable/i.test(button.textContent || ""))?.click();
      [...document.querySelectorAll("button")]
        .find((button) => /generate dry-run plan/i.test(button.textContent || ""))?.click();
      return true;
    })()`,
    settle: 2500,
  },
];

/** Overlays reached from the header rather than the tab bar. */
const OVERLAYS = [
  {
    slug: "settings",
    open: `[...document.querySelectorAll(".header__meta button")]
      .find((button) => button.textContent.trim() === "Settings")?.click(), true`,
    close: `[...document.querySelectorAll(".settings-dialog__head button")]
      .find((button) => button.textContent.trim() === "Close")?.click(), true`,
  },
];

function findBrowser() {
  const found = BROWSERS.find((path) => existsSync(path));
  if (!found) throw new Error("No Chrome or Edge installation found.");
  return found;
}

async function waitFor(check, { timeout = 20000, interval = 200, what = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    try {
      const value = await check();
      if (value) return value;
    } catch {
      /* keep polling */
    }
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(message.error.message));
        else resolve(message.result);
      } else if (message.method) {
        for (const listener of this.listeners) listener(message);
      }
    });
  }

  on(listener) {
    this.listeners.push(listener);
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timed out`));
        }
      }, 30000);
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "evaluate failed");
    }
    return result.result?.value;
  }
}

/** Runs inside the page: report layout problems the eye would catch. */
const AUDIT_SCRIPT = `(() => {
  const doc = document.documentElement;
  const problems = [];
  if (doc.scrollWidth > doc.clientWidth + 1) {
    problems.push({ kind: "page-overflow", detail: doc.scrollWidth + " > " + doc.clientWidth });
  }
  const viewportWidth = doc.clientWidth;
  for (const element of document.querySelectorAll("body *")) {
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    if (box.right > viewportWidth + 2 || box.left < -2) {
      const style = getComputedStyle(element);
      if (style.position === "fixed" || style.overflowX === "auto" || style.overflowX === "scroll") continue;
      problems.push({
        kind: "element-overflow",
        detail: (element.tagName.toLowerCase() + "." + String(element.className || "").split(" ").filter(Boolean).slice(0, 2).join(".")).slice(0, 90)
          + " left=" + Math.round(box.left) + " right=" + Math.round(box.right),
      });
    }
  }
  const seen = new Set();
  const unique = problems.filter((problem) => {
    const key = problem.kind + problem.detail;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return {
    problems: unique.slice(0, 12),
    overflowCount: unique.length,
    nodes: document.querySelectorAll("[data-node]").length,
    wires: document.querySelectorAll(".wire__line").length,
    ports: document.querySelectorAll("[data-port]").length,
    heading: document.querySelector("h1, .header__title")?.textContent?.trim() || null,
    activeTab: document.querySelector(".view-tabs .is-active strong")?.textContent?.trim() || null,
  };
})()`;

async function main() {
  const browser = findBrowser();
  const profile = join(tmpdir(), `switchops-visual-${Date.now()}`);
  mkdirSync(OUT_DIR, { recursive: true });

  const child = spawn(browser, [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "about:blank",
  ], { stdio: "ignore" });

  const report = { baseUrl: BASE_URL, checkedAt: new Date().toISOString(), views: [] };
  let socket;
  try {
    const version = await waitFor(
      async () => (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(),
      { what: "the browser debugging endpoint" },
    );

    socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    const browserCdp = new Cdp(socket);
    const { targetId } = await browserCdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await browserCdp.send("Target.attachToTarget", { targetId, flatten: true });

    // Flat session: wrap send so every call carries the sessionId.
    const page = new Cdp(socket);
    page.nextId = 5000;
    const rawSend = page.send.bind(page);
    page.send = (method, params = {}) => {
      const id = page.nextId++;
      return new Promise((resolve, reject) => {
        page.pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params, sessionId }));
        setTimeout(() => {
          if (page.pending.has(id)) {
            page.pending.delete(id);
            reject(new Error(`${method} timed out`));
          }
        }, 30000);
      });
    };
    void rawSend;

    const consoleMessages = [];
    const failedRequests = [];
    page.on((message) => {
      if (message.sessionId !== sessionId) return;
      if (message.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(message.params.type)) {
        consoleMessages.push({
          type: message.params.type,
          text: message.params.args.map((arg) => arg.value ?? arg.description ?? "").join(" ").slice(0, 300),
        });
      }
      if (message.method === "Runtime.exceptionThrown") {
        consoleMessages.push({
          type: "exception",
          text: (message.params.exceptionDetails?.exception?.description || "exception").slice(0, 300),
        });
      }
      if (message.method === "Network.loadingFailed") {
        failedRequests.push(message.params.errorText);
      }
    });

    await page.send("Runtime.enable");
    await page.send("Page.enable");
    await page.send("Network.enable");

    for (const viewport of VIEWPORTS) {
      await page.send("Emulation.setDeviceMetricsOverride", {
        width: viewport.width,
        height: viewport.height,
        deviceScaleFactor: 1,
        mobile: false,
      });
      await page.send("Page.navigate", { url: BASE_URL });
      await waitFor(
        () => page.evaluate(`!!document.querySelector(".view-tabs button")`),
        { what: "the dashboard to render" },
      );
      await new Promise((resolve) => setTimeout(resolve, 900));

      for (const view of VIEWS) {
        const clicked = await page.evaluate(`(() => {
          const button = [...document.querySelectorAll(".view-tabs button")]
            .find((candidate) => candidate.textContent.trim().startsWith(${JSON.stringify(view.label)}));
          if (!button) return false;
          button.click();
          return true;
        })()`);
        if (!clicked) {
          report.views.push({ view: view.slug, viewport: viewport.name, error: "tab not found" });
          continue;
        }
        await new Promise((resolve) => setTimeout(resolve, 700));
        if (view.prepare) {
          await page.evaluate(view.prepare);
          await new Promise((resolve) => setTimeout(resolve, view.settle || 900));
        }
        const before = consoleMessages.length;
        const audit = await page.evaluate(AUDIT_SCRIPT);
        const shot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
        const file = join(OUT_DIR, `${viewport.name}-${view.slug}.png`);
        writeFileSync(file, Buffer.from(shot.data, "base64"));
        report.views.push({
          view: view.slug,
          viewport: viewport.name,
          file,
          ...audit,
          newConsoleMessages: consoleMessages.slice(before),
        });
      }

      for (const overlay of OVERLAYS) {
        await page.evaluate(overlay.open);
        await new Promise((resolve) => setTimeout(resolve, 700));
        const before = consoleMessages.length;
        const audit = await page.evaluate(AUDIT_SCRIPT);
        const shot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
        const file = join(OUT_DIR, `${viewport.name}-${overlay.slug}.png`);
        writeFileSync(file, Buffer.from(shot.data, "base64"));
        report.views.push({
          view: overlay.slug,
          viewport: viewport.name,
          file,
          ...audit,
          newConsoleMessages: consoleMessages.slice(before),
        });
        await page.evaluate(overlay.close);
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    }

    report.consoleMessages = consoleMessages;
    report.failedRequests = failedRequests;
    writeFileSync(join(OUT_DIR, "report.json"), JSON.stringify(report, null, 2));

    // Console summary.
    let problems = 0;
    for (const entry of report.views) {
      const flags = [];
      if (entry.error) flags.push(entry.error);
      if (entry.overflowCount) flags.push(`${entry.overflowCount} overflow`);
      if (entry.newConsoleMessages?.length) flags.push(`${entry.newConsoleMessages.length} console`);
      problems += flags.length;
      console.log(
        `${entry.viewport.padEnd(8)} ${entry.view.padEnd(15)} ` +
        `nodes=${entry.nodes ?? "-"} wires=${entry.wires ?? "-"} ports=${entry.ports ?? "-"} ` +
        (flags.length ? `!! ${flags.join(", ")}` : "ok"),
      );
      for (const problem of entry.problems || []) console.log(`           ${problem.kind}: ${problem.detail}`);
    }
    console.log(`\nconsole errors/warnings: ${consoleMessages.length}`);
    for (const message of consoleMessages.slice(0, 12)) console.log(`  [${message.type}] ${message.text}`);
    console.log(`failed requests: ${failedRequests.length}`);
    console.log(`screenshots: ${OUT_DIR}`);
    process.exitCode = problems || consoleMessages.length ? 1 : 0;
  } finally {
    try { socket?.close(); } catch { /* closing best effort */ }
    child.kill();
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* temp profile */ }
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(2);
});
