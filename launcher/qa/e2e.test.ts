// @vitest-environment node
import { describe, it, expect, beforeAll, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { JSDOM } from "jsdom";
import { buildLauncher } from "../scripts/build.mjs";

const TILE_STEP = 300;
const TILE_WIDTH = 250;
const LAUNCH_URI = "luna://com.webos.applicationManager/launch";
const WEATHER_OK = '{"current":{"temperature_2m":17.6,"weather_code":2,"is_day":1}}';

let dir = "";
const doms: JSDOM[] = [];

beforeAll(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "launcher-qa-"));
  await buildLauncher(dir);
  return () => fs.rmSync(dir, { recursive: true, force: true });
});
afterEach(() => {
  while (doms.length) doms.pop()?.window.close();
});

interface Harness {
  dom: JSDOM;
  launches: string[];
  backs: number[];
  xhrs: FakeXhr[];
}

class FakeXhr {
  static all: FakeXhr[] = [];
  timeout = 0;
  status = 0;
  responseText = "";
  url = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  open(_method: string, url: string): void {
    this.url = url;
  }
  send(): void {
    FakeXhr.all.push(this);
  }
  respond(status: number, body: string): void {
    this.status = status;
    this.responseText = body;
    this.onload?.();
  }
}

interface BootOptions {
  stored?: string;
  withBridge?: boolean;
  withPlatform?: boolean;
  bridgeReply?: string | null;
}

function boot(options: BootOptions = {}): Harness {
  const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
  const code = fs.readFileSync(path.join(dir, "main.js"), "utf8");
  const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true, url: "http://localhost/" });
  doms.push(dom);
  const w = dom.window as unknown as Record<string, unknown>;
  const launches: string[] = [];
  const backs: number[] = [];
  FakeXhr.all = [];
  w.XMLHttpRequest = FakeXhr;
  if (options.withBridge !== false) {
    const reply = options.bridgeReply === undefined ? '{"returnValue":true}' : options.bridgeReply;
    w.PalmServiceBridge = class {
      onservicecallback: ((m: string) => void) | null = null;
      call(uri: string, payload: string): void {
        launches.push(uri + " " + payload);
        if (reply !== null) this.onservicecallback?.(reply);
      }
    };
  }
  if (options.withPlatform !== false) {
    w.webOSSystem = { platformBack: () => backs.push(1) };
  }
  if (options.stored !== undefined) dom.window.localStorage.setItem("launcher.lastIndex", options.stored);
  // jsdom does no layout: emulate it by tile position so the slide can be observed.
  Object.defineProperty(dom.window.HTMLElement.prototype, "offsetLeft", {
    configurable: true,
    get(this: HTMLElement) {
      const parent = this.parentElement;
      return parent ? Array.prototype.indexOf.call(parent.children, this) * TILE_STEP : 0;
    },
  });
  Object.defineProperty(dom.window.HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => TILE_WIDTH,
  });
  dom.window.eval(code);
  return { dom, launches, backs, xhrs: FakeXhr.all };
}

function press(dom: JSDOM, keyCode: number): Event {
  const ev = new dom.window.KeyboardEvent("keydown", { cancelable: true, bubbles: true });
  Object.defineProperty(ev, "keyCode", { value: keyCode });
  dom.window.document.dispatchEvent(ev);
  return ev;
}
const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));
const q = (dom: JSDOM, sel: string): Element | null => dom.window.document.querySelector(sel);
const text = (dom: JSDOM, role: string): string | null | undefined => q(dom, `[data-role="${role}"]`)?.textContent;
const focusedId = (dom: JSDOM): string | null | undefined => q(dom, ".is-focused")?.getAttribute("data-tile-id");
const row = (dom: JSDOM): HTMLElement => q(dom, '[data-role="app-row"]') as HTMLElement;

describe("QA e2e on the built bundle", () => {
  it("renders the bundled default config: 6 tiles in names style, first focused, veil and background set", () => {
    const { dom } = boot();
    expect(dom.window.document.querySelectorAll('[data-role="app-tile"]').length).toBe(6);
    expect(row(dom).getAttribute("data-style")).toBe("names");
    expect(focusedId(dom)).toBe("com.webos.app.livetv");
    expect((q(dom, '[data-role="background"]') as HTMLElement).style.backgroundImage).toContain("backgrounds/night.jpg");
    expect((q(dom, '[data-role="veil"]') as HTMLElement).style.opacity).toBe("0.8");
    expect(text(dom, "clock-time")).toMatch(/^\d\d:\d\d$/);
    expect(text(dom, "clock-meridiem")).toBe("");
    expect(q(dom, '[data-role="error"]')).toBeNull();
  });

  it("requests Open-Meteo once at start for Madrid and shows temperature and icon on success", async () => {
    const { dom, xhrs } = boot();
    expect(xhrs.length).toBe(1);
    expect(xhrs[0].url).toBe(
      "https://api.open-meteo.com/v1/forecast?latitude=40.4168&longitude=-3.7038&current=temperature_2m,weather_code,is_day&timezone=auto"
    );
    expect(xhrs[0].timeout).toBe(8000);
    expect(text(dom, "weather")).toBe("Madrid");
    xhrs[0].respond(200, WEATHER_OK);
    await flush();
    expect(text(dom, "weather")).toContain("Madrid · 18°");
    expect(q(dom, '[data-role="weather-icon"][data-icon="cloud"] svg')).not.toBeNull();
  });

  it("shows only the city and no error when the weather call fails (HTTP 500, network error, timeout, garbage)", async () => {
    const attempts: Array<(x: FakeXhr) => void> = [
      (x) => x.respond(500, ""),
      (x) => x.onerror?.(),
      (x) => x.ontimeout?.(),
      (x) => x.respond(200, "<html>captive portal</html>"),
      (x) => x.respond(200, '{"current":{"temperature_2m":"hot","weather_code":2,"is_day":1}}'),
    ];
    for (const attempt of attempts) {
      const { dom, xhrs } = boot();
      attempt(xhrs[0]);
      await flush();
      expect(text(dom, "weather")).toBe("Madrid");
      expect(q(dom, '[data-role="weather-icon"]')).toBeNull();
      expect(q(dom, '[data-role="error"]')).toBeNull();
    }
  });

  it("d1 scenario: the row slides so the focused tile stays inside the 1696 px viewport, and slides back", () => {
    const { dom } = boot();
    expect(row(dom).style.transform).toBe("translateX(-0px)");
    for (let i = 0; i < 5; i++) press(dom, 39);
    const right = 5 * TILE_STEP + TILE_WIDTH - 1696;
    expect(focusedId(dom)).toBe("web:https://elpais.com");
    expect(row(dom).style.transform).toBe(`translateX(-${right}px)`);
    for (let i = 0; i < 5; i++) press(dom, 37);
    expect(focusedId(dom)).toBe("com.webos.app.livetv");
    expect(row(dom).style.transform).toBe("translateX(-0px)");
  });

  it("the row element survives focus moves and clock ticks (needed for the CSS transition)", () => {
    const { dom } = boot();
    const first = row(dom);
    press(dom, 39);
    press(dom, 39);
    expect(row(dom)).toBe(first);
  });

  it("OK launches the focused app through PalmServiceBridge and stores its index", () => {
    const { dom, launches } = boot();
    press(dom, 39);
    press(dom, 39);
    press(dom, 13);
    expect(launches).toEqual([`${LAUNCH_URI} {"id":"netflix"}`]);
    expect(dom.window.localStorage.getItem("launcher.lastIndex")).toBe("2");
  });

  it("OK on an input tile and on the web tile send the right payloads", async () => {
    const a = boot();
    press(a.dom, 39);
    press(a.dom, 39);
    press(a.dom, 39);
    press(a.dom, 39);
    press(a.dom, 13);
    expect(a.launches).toEqual([`${LAUNCH_URI} {"id":"com.webos.app.hdmi1"}`]);
    const b = boot();
    for (let i = 0; i < 6; i++) press(b.dom, 39);
    press(b.dom, 13);
    expect(b.launches).toEqual([
      `${LAUNCH_URI} {"id":"com.webos.app.browser","params":{"target":"https://elpais.com"}}`,
    ]);
  });

  it("the last opened tile is focused on the next start; a corrupt stored value falls back to the first", () => {
    expect(focusedId(boot({ stored: "3" }).dom)).toBe("amazon");
    expect(focusedId(boot({ stored: "99" }).dom)).toBe("com.webos.app.livetv");
    expect(focusedId(boot({ stored: "abc" }).dom)).toBe("com.webos.app.livetv");
    expect(focusedId(boot({ stored: "-2" }).dom)).toBe("com.webos.app.livetv");
  });

  it("Down opens LG Home, Up does nothing, Back calls platformBack, unknown key is not prevented", () => {
    const { dom, launches, backs } = boot();
    expect(press(dom, 40).defaultPrevented).toBe(true);
    expect(launches).toEqual([`${LAUNCH_URI} {"id":"com.webos.app.home"}`]);
    expect(press(dom, 38).defaultPrevented).toBe(true);
    expect(launches.length).toBe(1);
    expect(press(dom, 461).defaultPrevented).toBe(true);
    expect(backs.length).toBe(1);
    expect(press(dom, 65).defaultPrevented).toBe(false);
    expect(backs.length).toBe(1);
  });

  it("Back without webOSSystem or PalmSystem does not throw", () => {
    const { dom } = boot({ withPlatform: false });
    expect(() => press(dom, 461)).not.toThrow();
  });

  it("without PalmServiceBridge a launch fails visibly and an arrow clears the message", async () => {
    const { dom } = boot({ withBridge: false });
    press(dom, 13);
    await flush();
    expect(text(dom, "error")).toBe("No se pudo abrir TV");
    press(dom, 39);
    expect(q(dom, '[data-role="error"]')).toBeNull();
  });

  it("a failing Home launch shows the Home error and a negative reply from webOS is a failure", async () => {
    const { dom } = boot({ bridgeReply: '{"returnValue":false,"errorText":"nope"}' });
    press(dom, 40);
    await flush();
    expect(text(dom, "error")).toBe("No se pudo abrir el Home de LG");
  });

  it("a launch that never answers blocks further launches (in-flight guard) but not navigation", () => {
    const { dom, launches } = boot({ bridgeReply: null });
    press(dom, 13);
    press(dom, 13);
    press(dom, 40);
    expect(launches.length).toBe(1);
    press(dom, 39);
    expect(focusedId(dom)).toBe("youtube.leanback.v4");
  });

  it("holding Right past the last tile and Left past the first never leaves the range", () => {
    const { dom } = boot();
    for (let i = 0; i < 20; i++) press(dom, 39);
    expect(focusedId(dom)).toBe("web:https://elpais.com");
    for (let i = 0; i < 20; i++) press(dom, 37);
    expect(focusedId(dom)).toBe("com.webos.app.livetv");
  });
});
