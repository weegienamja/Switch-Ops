/**
 * Headless browser acceptance matrix for every v0.8 Lab Assurance view.
 *
 * This is deterministic UI evidence only. It uses the source-only mock backend
 * plus synthetic API states and must never be recorded as real-device or human
 * interactive acceptance.
 */
import { spawn } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import net from "node:net";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = join(ROOT, ".visual");
const NOW = "2026-08-24T12:00:00Z";
const BROWSERS = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];
const TABS = [
  ["Overview", "overview"],
  ["Topology", "topology"],
  ["Paths", "paths"],
  ["Failure domains", "failures"],
  ["Performance", "performance"],
  ["Segmentation", "segmentation"],
  ["Design findings", "findings"],
  ["Capabilities", "capabilities"],
];
const VIEWPORTS = {
  desktop: { width: 1440, height: 1000 },
  tablet: { width: 900, height: 1050 },
  mobile: { width: 390, height: 844 },
};

function device(id, collectionState = "CURRENT", label = `Fixture device ${id}`) {
  return {
    id,
    label,
    role: "SWITCH",
    provider: "cisco-ios",
    model: "Fixture model",
    software: "Fixture release",
    primary: id === "primary-fixture",
    observed: collectionState !== "FAILED",
    collectionState,
    detail: collectionState === "FAILED" ? "The source failed independently; no current claims are emitted." : "Current fixture evidence is independently attributed.",
    evidenceIds: [`ev-${id}`],
  };
}

function baseState(overrides = {}) {
  return {
    generatedAt: NOW,
    collectionState: "CURRENT",
    summary: {
      observedDevices: 2,
      physicalEdges: 1,
      logicalNetworks: 1,
      criticalFindings: 0,
      warningFindings: 1,
      unknownFindings: 0,
      evidenceGaps: 1,
    },
    devices: [device("primary-fixture"), device("secondary-fixture")],
    interfaces: [],
    edges: [{
      id: "edge-fixture",
      fromNodeId: "primary-fixture",
      toNodeId: "secondary-fixture",
      fromInterface: "Gi1/0/1",
      toInterface: "Gi1/0/1",
      kind: "PHYSICAL",
      state: "PROVEN",
      confidence: "CONFIRMED",
      reciprocal: true,
      detail: "Reciprocal fixture discovery agrees.",
      evidenceIds: ["ev-primary-fixture", "ev-secondary-fixture"],
    }],
    logicalNetworks: [{
      id: "logical-fixture",
      vlanId: 10,
      name: "Fixture broadcast domain",
      vrf: null,
      gatewayNodes: [],
      memberInterfaces: [],
      trunkInterfaces: ["primary-fixture:Gi1/0/1"],
      endpointNodes: [],
      isolationState: "POLICY_UNKNOWN",
      detail: "Broadcast-domain separation does not prove policy isolation.",
      evidenceIds: ["ev-primary-fixture"],
    }],
    capabilities: [{
      id: "cap-fixture",
      deviceId: "primary-fixture",
      name: "Spanning Tree",
      state: "SUPPORTED",
      configured: true,
      observed: true,
      detail: "Current fixture evidence proves support.",
      evidenceIds: ["ev-primary-fixture"],
    }],
    findings: [{
      id: "finding-fixture",
      category: "EVIDENCE",
      severity: "WARNING",
      confidence: "HIGH",
      title: "Fixture finding",
      detail: "The wording is bounded to current evidence.",
      consequence: "A dependency may exist only where the graph shows it.",
      remediation: "Confirm against the lab.",
      affectedIds: ["primary-fixture"],
      evidenceIds: ["ev-primary-fixture"],
    }],
    failures: [{
      id: "failure-fixture",
      targetId: "edge-fixture",
      targetKind: "UPLINK",
      title: "Loss of a fixture relationship",
      confidence: "CONFIRMED",
      consequences: ["One observed graph relationship is removed; protocol reconvergence remains unknown."],
      affectedIds: ["secondary-fixture"],
      controlImpact: "Control impact follows only the current evidence graph.",
      evidenceIds: ["ev-primary-fixture"],
    }],
    paths: [{
      id: "path-fixture",
      fromNodeId: "primary-fixture",
      toNodeId: "secondary-fixture",
      state: "PROVEN",
      summary: "One evidence-backed hop.",
      hops: [
        { nodeId: "primary-fixture", label: "Fixture primary", viaInterface: null, state: "PROVEN", evidenceIds: [] },
        { nodeId: "secondary-fixture", label: "Fixture secondary", viaInterface: "Gi1/0/1", state: "PROVEN", evidenceIds: ["ev-primary-fixture"] },
      ],
      evidenceIds: ["ev-primary-fixture"],
    }],
    performance: [{
      id: "probe-fixture",
      targetLabel: "Fixture target",
      targetToken: "fixture-token",
      state: "HEALTHY",
      observedAt: NOW,
      transmitted: 4,
      received: 4,
      lossPercent: 0,
      latencyAvgMs: 1,
      jitterMs: 0,
      routeChanged: false,
      detail: "Bounded fixture probe completed.",
    }],
    evidence: [{
      id: "ev-primary-fixture",
      deviceId: "primary-fixture",
      kind: "OBSERVED",
      command: "show_version",
      confidence: "CONFIRMED",
      observedAt: NOW,
      current: true,
      detail: "Current fixture evidence.",
    }],
    limitations: ["Fixture evidence never closes a real-device or interactive acceptance gate."],
    ...overrides,
  };
}

const empty = baseState({
  collectionState: "NOT_COLLECTED",
  summary: { observedDevices: 0, physicalEdges: 0, logicalNetworks: 0, criticalFindings: 0, warningFindings: 0, unknownFindings: 0, evidenceGaps: 0 },
  devices: [],
  edges: [],
  logicalNetworks: [],
  capabilities: [],
  findings: [],
  failures: [],
  paths: [],
  performance: [],
  evidence: [],
  limitations: [],
});

const partial = baseState({
  collectionState: "PARTIAL",
  devices: [device("primary-fixture"), device("secondary-fixture", "PARTIAL")],
});

const failedSource = baseState({
  collectionState: "PARTIAL",
  devices: [device("primary-fixture"), device("secondary-fixture", "FAILED")],
  evidence: [
    baseState().evidence[0],
    { id: "ev-secondary-fixture", deviceId: "secondary-fixture", kind: "TRANSPORT_FAILED", command: "show_version", confidence: "UNKNOWN", observedAt: NOW, current: false, detail: "The secondary session was unavailable." },
  ],
});

const unknown = baseState({
  collectionState: "PARTIAL",
  devices: [device("primary-fixture"), device("secondary-fixture", "PARTIAL")],
  edges: [],
  logicalNetworks: [{ ...baseState().logicalNetworks[0], isolationState: "UNKNOWN" }],
  capabilities: [{ ...baseState().capabilities[0], state: "UNKNOWN", configured: null, observed: null, detail: "Available evidence cannot decide." }],
  findings: [{ ...baseState().findings[0], severity: "UNKNOWN", confidence: "UNKNOWN", title: "Unknown evidence boundary" }],
  paths: [{
    ...baseState().paths[0],
    state: "UNKNOWN",
    summary: "The path stops because the next hop is unknown.",
    hops: [
      { nodeId: "primary-fixture", label: "Fixture primary", viaInterface: null, state: "PROVEN", evidenceIds: [] },
      { nodeId: "secondary-fixture", label: "Unknown next hop", viaInterface: null, state: "UNKNOWN", evidenceIds: [] },
    ],
  }],
});

const longText = "LONG-CONTENT-MARKER " + "evidence-boundary-".repeat(22);
const longContent = baseState({
  summary: { observedDevices: 10, physicalEdges: 9, logicalNetworks: 12, criticalFindings: 1, warningFindings: 12, unknownFindings: 8, evidenceGaps: 20 },
  devices: Array.from({ length: 10 }, (_, index) => device(index === 0 ? "primary-fixture" : `device-${index}`, "CURRENT", `${longText}${index}`)),
  edges: Array.from({ length: 9 }, (_, index) => ({
    ...baseState().edges[0],
    id: `long-edge-${index}`,
    fromNodeId: index === 0 ? "primary-fixture" : `device-${index}`,
    toNodeId: `device-${index + 1}`,
    reciprocal: index % 2 === 0,
    confidence: index % 2 === 0 ? "CONFIRMED" : "HIGH",
    detail: `${longText}${index}`,
  })),
  logicalNetworks: Array.from({ length: 12 }, (_, index) => ({ ...baseState().logicalNetworks[0], id: `long-network-${index}`, vlanId: index + 10, name: `${longText}${index}`, isolationState: index % 2 ? "POLICY_UNKNOWN" : "UNKNOWN" })),
  capabilities: Array.from({ length: 32 }, (_, index) => ({ ...baseState().capabilities[0], id: `long-cap-${index}`, deviceId: index % 2 ? "device-1" : "primary-fixture", name: `${longText}${index}`, state: index % 3 === 0 ? "UNKNOWN" : index % 3 === 1 ? "SUPPORTED" : "UNSUPPORTED", configured: index % 3 === 1 ? true : null, observed: index % 3 === 1 ? true : null, detail: `${longText}${index}` })),
  findings: Array.from({ length: 16 }, (_, index) => ({ ...baseState().findings[0], id: `long-finding-${index}`, title: `${longText}${index}`, detail: `${longText}${index}`, consequence: `${longText}${index}`, severity: index % 3 === 0 ? "UNKNOWN" : "WARNING" })),
  failures: Array.from({ length: 12 }, (_, index) => ({ ...baseState().failures[0], id: `long-failure-${index}`, title: `${longText}${index}`, consequences: [`${longText}${index}`], controlImpact: `${longText}${index}` })),
  paths: [{ ...baseState().paths[0], summary: longText, hops: Array.from({ length: 8 }, (_, index) => ({ nodeId: `path-node-${index}`, label: `${longText}${index}`, viaInterface: index ? `Gi1/0/${index}` : null, state: index === 7 ? "UNKNOWN" : "INFERRED", evidenceIds: [] })) }],
  performance: Array.from({ length: 12 }, (_, index) => ({ ...baseState().performance[0], id: `long-probe-${index}`, targetLabel: `${longText}${index}`, detail: `${longText}${index}` })),
  limitations: Array.from({ length: 12 }, (_, index) => `${longText}${index}`),
});

const SCENARIOS = {
  empty: { state: empty, devices: { keyringAvailable: true, devices: [] } },
  partial: { state: partial, devices: { keyringAvailable: true, devices: [{ id: "primary-fixture", label: "Fixture primary", primary: true, deviceType: "cisco_ios", storage: "legacy", configured: true }, { id: "secondary-fixture", label: "Fixture secondary", primary: false, deviceType: "cisco_xe", storage: "keyring", configured: true }] } },
  "failed-source": { state: failedSource, devices: { keyringAvailable: true, devices: [{ id: "primary-fixture", label: "Fixture primary", primary: true, deviceType: "cisco_ios", storage: "legacy", configured: true }, { id: "secondary-fixture", label: "Fixture secondary", primary: false, deviceType: "cisco_xe", storage: "keyring", configured: true }] } },
  unknown: { state: unknown, devices: { keyringAvailable: false, devices: [{ id: "primary-fixture", label: "Fixture primary", primary: true, deviceType: "cisco_ios", storage: "legacy", configured: true }] } },
  long: { state: longContent, devices: { keyringAvailable: true, devices: Array.from({ length: 8 }, (_, index) => ({ id: index ? `device-${index}` : "primary-fixture", label: `${longText}${index}`, primary: index === 0, deviceType: index % 2 ? "cisco_xe" : "cisco_ios", storage: index === 0 ? "legacy" : "keyring", configured: true })) } },
};

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolvePort(port));
    });
  });
}

async function waitFor(check, { timeout = 60000, interval = 200, what = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    try {
      const value = await check();
      if (value) return value;
    } catch {}
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}.`);
    await new Promise((resolveWait) => setTimeout(resolveWait, interval));
  }
}

class Cdp {
  constructor(socket, sessionId = null) {
    this.socket = socket;
    this.sessionId = sessionId;
    this.nextId = sessionId ? 5000 : 1;
    this.pending = new Map();
    this.listeners = [];
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
      } else if (message.method && (!this.sessionId || message.sessionId === this.sessionId)) {
        for (const listener of this.listeners) listener(message);
      }
    });
  }

  on(listener) { this.listeners.push(listener); }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolveSend, reject) => {
      this.pending.set(id, { resolve: resolveSend, reject });
      const message = { id, method, params };
      if (this.sessionId) message.sessionId = this.sessionId;
      this.socket.send(JSON.stringify(message));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timed out.`));
        }
      }, 30000);
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
    return result.result?.value;
  }
}

function startProxy(port, backendPort, frontendOrigin, getScenario) {
  const server = createServer((incoming, outgoing) => {
    const cors = {
      "access-control-allow-origin": frontendOrigin,
      "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
      "access-control-allow-headers": "content-type",
    };
    if (incoming.method === "OPTIONS") {
      outgoing.writeHead(204, cors);
      outgoing.end();
      return;
    }
    const path = incoming.url || "/";
    const fixture = getScenario();
    if (path === "/api/lab-assurance/state" && incoming.method === "GET") {
      outgoing.writeHead(200, { ...cors, "content-type": "application/json" });
      outgoing.end(JSON.stringify(fixture.state));
      return;
    }
    if (path === "/api/lab-assurance/devices" && incoming.method === "GET") {
      outgoing.writeHead(200, { ...cors, "content-type": "application/json" });
      outgoing.end(JSON.stringify(fixture.devices));
      return;
    }
    if (path === "/api/lab-assurance/refresh" && incoming.method === "POST") {
      outgoing.writeHead(200, { ...cors, "content-type": "application/json" });
      outgoing.end(JSON.stringify({ accepted: true, state: fixture.state }));
      return;
    }
    const upstream = httpRequest({
      host: "127.0.0.1",
      port: backendPort,
      path,
      method: incoming.method,
      headers: { ...incoming.headers, host: `127.0.0.1:${backendPort}` },
    }, (response) => {
      const headers = { ...response.headers, ...cors };
      delete headers["content-length"];
      outgoing.writeHead(response.statusCode || 502, headers);
      response.pipe(outgoing);
    });
    upstream.on("error", () => {
      if (!outgoing.headersSent) outgoing.writeHead(502, { ...cors, "content-type": "application/json" });
      outgoing.end(JSON.stringify({ detail: "Fixture backend unavailable." }));
    });
    incoming.pipe(upstream);
  });
  return new Promise((resolveServer, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolveServer(server));
  });
}

function spawnTracked(command, args, options, processes, logs) {
  const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
  processes.push(child);
  for (const stream of [child.stdout, child.stderr]) {
    stream.on("data", (chunk) => {
      logs.push(chunk.toString());
      if (logs.length > 120) logs.splice(0, logs.length - 120);
    });
  }
  return child;
}

const AUDIT = `(() => {
  const doc = document.documentElement;
  const problems = [];
  if (doc.scrollWidth > doc.clientWidth + 1) problems.push("page-overflow:" + doc.scrollWidth + ">" + doc.clientWidth);
  const hasScroller = (element) => {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const overflow = getComputedStyle(parent).overflowX;
      if ((overflow === "auto" || overflow === "scroll") && parent.scrollWidth > parent.clientWidth) return true;
    }
    return false;
  };
  for (const element of document.querySelectorAll(".lab-assurance *")) {
    const box = element.getBoundingClientRect();
    if (!box.width || !box.height || hasScroller(element)) continue;
    if (box.right > doc.clientWidth + 2 || box.left < -2) {
      problems.push("element-overflow:" + element.tagName.toLowerCase() + "." + String(element.className || "").split(" ").slice(0, 2).join("."));
    }
  }
  return {
    problems: [...new Set(problems)].slice(0, 20),
    activeView: document.querySelector(".lab-assurance")?.dataset.assuranceView || null,
    collectionState: document.querySelector(".lab-assurance")?.dataset.collectionState || null,
    text: document.querySelector(".lab-assurance")?.innerText || "",
  };
})()`;

async function main() {
  const browser = BROWSERS.find(existsSync);
  if (!browser) throw new Error("No installed Chrome or Edge browser was found.");
  const python = join(ROOT, ".venv", "Scripts", "python.exe");
  const nextBin = join(ROOT, "frontend", "node_modules", "next", "dist", "bin", "next");
  if (!existsSync(python) || !existsSync(nextBin)) throw new Error("The source development dependencies are not installed.");

  const [backendPort, proxyPort, frontendPort, browserPort] = await Promise.all([freePort(), freePort(), freePort(), freePort()]);
  const frontendOrigin = `http://127.0.0.1:${frontendPort}`;
  const runtimeRoot = mkdtempSync(join(tmpdir(), "switchops-v08-e2e-data-"));
  const browserProfile = mkdtempSync(join(tmpdir(), "switchops-v08-e2e-browser-"));
  const processes = [];
  const processLogs = [];
  let currentScenario = "empty";
  let proxy;
  let socket;
  const report = { checkedAt: new Date().toISOString(), automatedOnly: true, humanInteractiveGateClosed: false, checks: [], failures: [] };

  try {
    spawnTracked(python, ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", String(backendPort), "--log-level", "warning"], {
      cwd: ROOT,
      env: {
        ...process.env,
        SWITCH_MOCK_MODE: "true",
        SWITCHOPS_DATA_ROOT: runtimeRoot,
        SWITCHOPS_CORS_ORIGINS: frontendOrigin,
      },
    }, processes, processLogs);
    await waitFor(async () => (await fetch(`http://127.0.0.1:${backendPort}/health`)).ok, { what: "fixture backend" });
    proxy = await startProxy(proxyPort, backendPort, frontendOrigin, () => SCENARIOS[currentScenario]);

    spawnTracked(process.execPath, [nextBin, "dev", "-H", "127.0.0.1", "-p", String(frontendPort)], {
      cwd: join(ROOT, "frontend"),
      env: { ...process.env, NEXT_PUBLIC_BACKEND_URL: `http://127.0.0.1:${proxyPort}` },
    }, processes, processLogs);
    await waitFor(async () => (await fetch(frontendOrigin)).ok, { timeout: 120000, what: "Next development server" });

    spawnTracked(browser, [
      "--headless=new",
      `--remote-debugging-port=${browserPort}`,
      `--user-data-dir=${browserProfile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-extensions",
      "--force-device-scale-factor=1",
      "about:blank",
    ], {}, processes, processLogs);
    const version = await waitFor(async () => (await fetch(`http://127.0.0.1:${browserPort}/json/version`)).json(), { what: "browser debugging endpoint" });
    socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolveOpen, reject) => {
      socket.addEventListener("open", resolveOpen, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    const root = new Cdp(socket);
    const { targetId } = await root.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await root.send("Target.attachToTarget", { targetId, flatten: true });
    const page = new Cdp(socket, sessionId);
    const consoleMessages = [];
    const failedRequests = [];
    page.on((message) => {
      if (message.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(message.params.type)) {
        consoleMessages.push(message.params.args.map((item) => item.value ?? item.description ?? "").join(" ").slice(0, 400));
      }
      if (message.method === "Runtime.exceptionThrown") {
        consoleMessages.push((message.params.exceptionDetails?.exception?.description || "browser exception").slice(0, 400));
      }
      if (message.method === "Network.loadingFailed" && !message.params.canceled && !/ERR_ABORTED/.test(message.params.errorText || "")) {
        failedRequests.push(message.params.errorText || "request failed");
      }
    });
    await page.send("Runtime.enable");
    await page.send("Page.enable");
    await page.send("Network.enable");

    const matrix = [
      ...Object.keys(SCENARIOS).map((scenario) => ({ scenario, viewport: "desktop" })),
      { scenario: "long", viewport: "tablet" },
      { scenario: "long", viewport: "mobile" },
    ];
    for (const item of matrix) {
      console.log(`Checking Lab Assurance ${item.scenario}/${item.viewport}...`);
      currentScenario = item.scenario;
      const viewport = VIEWPORTS[item.viewport];
      await page.send("Emulation.setDeviceMetricsOverride", { ...viewport, deviceScaleFactor: 1, mobile: item.viewport === "mobile" });
      const consoleStart = consoleMessages.length;
      const requestStart = failedRequests.length;
      await page.send("Page.navigate", { url: frontendOrigin });
      await waitFor(() => page.evaluate(`!!document.querySelector(".view-tabs button")`), { timeout: 60000, what: `${item.scenario}/${item.viewport} dashboard` });
      const opened = await page.evaluate(`(() => {
        const button = [...document.querySelectorAll(".view-tabs button")].find((item) => item.textContent.trim().startsWith("Lab Assurance"));
        if (!button) return false;
        button.click();
        return true;
      })()`);
      if (!opened) throw new Error("Lab Assurance navigation was not found.");
      await waitFor(() => page.evaluate(`document.querySelector(".lab-assurance")?.dataset.assuranceView === "overview"`), { what: `${item.scenario}/${item.viewport} Lab Assurance` });

      for (const [label, slug] of TABS) {
        const clicked = await page.evaluate(`(() => {
          const button = [...document.querySelectorAll(".assurance-tabs button")].find((item) => item.textContent.trim() === ${JSON.stringify(label)});
          if (!button) return false;
          button.click();
          return true;
        })()`);
        if (!clicked) throw new Error(`Lab Assurance tab ${label} was not found.`);
        await waitFor(() => page.evaluate(`document.querySelector(".lab-assurance")?.dataset.assuranceView === ${JSON.stringify(slug)}`), { what: `${item.scenario}/${slug}` });
        await new Promise((resolveSettle) => setTimeout(resolveSettle, 120));
        if (item.scenario === "long") {
          await page.evaluate(`window.scrollTo(0, document.documentElement.scrollHeight), true`);
          await new Promise((resolveSettle) => setTimeout(resolveSettle, 80));
        }
        const audit = await page.evaluate(AUDIT);
        const problems = [...audit.problems];
        if (item.scenario === "empty" && !/No |not sufficient|No active probes/.test(audit.text)) problems.push("empty-state-copy-missing");
        if (item.scenario === "partial" && audit.collectionState !== "PARTIAL") problems.push("partial-state-missing");
        if (item.scenario === "failed-source" && slug === "topology" && !audit.text.includes("FAILED")) problems.push("failed-source-state-missing");
        if (item.scenario === "unknown" && ["paths", "segmentation", "findings", "capabilities"].includes(slug) && !audit.text.includes("UNKNOWN")) problems.push("unknown-state-missing");
        if (item.scenario === "long" && !audit.text.includes("LONG-CONTENT-MARKER")) problems.push("long-content-not-rendered");
        const browserProblems = consoleMessages.slice(consoleStart).map((text) => `console:${text}`);
        const requestProblems = failedRequests.slice(requestStart).map((text) => `request:${text}`);
        const check = { scenario: item.scenario, viewport: item.viewport, view: slug, problems: [...problems, ...browserProblems, ...requestProblems] };
        report.checks.push(check);
        if (check.problems.length) report.failures.push(check);
      }
    }

    mkdirSync(OUT_DIR, { recursive: true });
    writeFileSync(join(OUT_DIR, "lab-assurance-e2e.json"), JSON.stringify(report, null, 2) + "\n", "utf8");
    if (report.failures.length) throw new Error(`${report.failures.length} Lab Assurance browser checks failed.`);
    console.log(`Lab Assurance browser E2E passed: ${report.checks.length} view/state/viewport checks.`);
  } catch (error) {
    mkdirSync(OUT_DIR, { recursive: true });
    report.runtimeError = error instanceof Error ? error.message : String(error);
    report.processLogTail = processLogs.join("").split(/\r?\n/).filter(Boolean).slice(-30);
    writeFileSync(join(OUT_DIR, "lab-assurance-e2e.json"), JSON.stringify(report, null, 2) + "\n", "utf8");
    throw error;
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close();
    const exits = [];
    for (const child of processes.reverse()) {
      if (child.exitCode === null) {
        exits.push(new Promise((resolveExit) => child.once("exit", resolveExit)));
      }
      if (!child.killed) child.kill();
    }
    await Promise.race([
      Promise.allSettled(exits),
      new Promise((resolveWait) => setTimeout(resolveWait, 3000)),
    ]);
    if (proxy) {
      proxy.closeAllConnections?.();
      await new Promise((resolveClose) => proxy.close(resolveClose));
    }
    rmSync(browserProfile, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 });
    rmSync(runtimeRoot, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 });
  }
}

await main();
