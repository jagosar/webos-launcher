import { describe, it, expect, vi, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { parseConfig, ConfigError } from "../src/config";
import { formatClockWithMode } from "../src/clock";
import { launchApp, launchTile, LaunchError } from "../src/launch";
import { loadLastIndex, saveLastIndex, getStorage } from "../src/system";
import { weatherIcon, xhrGet, fetchWeather, WeatherError } from "../src/weather";
import { createLauncherView } from "../src/view";
import { startLauncher, WEATHER_REFRESH_MS } from "../src/main";
import { BACKGROUND_IDS } from "../src/types";
import type { ServiceBridge, ViewModel, Tile } from "../src/types";

const SRC = path.join(__dirname, "..", "src");
const PUBLIC = path.join(__dirname, "..", "public");
const flush = (): Promise<void> => Promise.resolve().then(() => Promise.resolve());

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

describe("parseConfig edge cases", () => {
  it("accepts a deeply frozen raw object and never mutates it", () => {
    const raw = deepFreeze({ tiles: [{ id: " a ", title: " A " }], weather: { name: " X ", lat: 1, lon: 2 } });
    const cfg = parseConfig(raw);
    expect(cfg.tiles[0]).toEqual({ type: "app", id: "a", title: "A" });
    expect(cfg.weather).toEqual({ name: "X", lat: 1, lon: 2 });
  });

  it("omits optional keys instead of setting them to undefined", () => {
    const cfg = parseConfig({ tiles: [{ id: "a", title: "A" }, { type: "web", title: "W", url: "http://w" }] });
    expect(Object.keys(cfg.tiles[0])).toEqual(["type", "id", "title"]);
    expect(Object.keys(cfg.tiles[1]).sort()).toEqual(["id", "title", "type", "url"]);
    const withUndefinedIcon = parseConfig({ tiles: [{ id: "a", title: "A", icon: undefined }] });
    expect("icon" in withUndefinedIcon.tiles[0]).toBe(false);
  });

  it("ignores a web tile's own id and any unknown keys", () => {
    const cfg = parseConfig({ extra: 1, tiles: [{ type: "web", id: "zzz", title: "W", url: "HTTPS://x.test", foo: 1 }] });
    expect(cfg.tiles[0]).toEqual({ type: "web", id: "web:HTTPS://x.test", title: "W", url: "HTTPS://x.test" });
  });

  it.each([
    [null], [undefined], ["x"], [42], [[]], [true],
  ])("rejects non-object raw %j", (raw) => {
    expect(() => parseConfig(raw)).toThrow(/debe ser un objeto/);
  });

  it.each([
    [{ tiles: "a" }], [{ tiles: {} }], [{ tiles: null }], [{}],
  ])("rejects bad tiles %j", (raw) => {
    expect(() => parseConfig(raw)).toThrow(/tiles debe ser un array/);
  });

  it.each([
    [{ tiles: [null] }], [{ tiles: ["x"] }], [{ tiles: [[]] }],
    [{ tiles: [{ id: "  ", title: "A" }] }], [{ tiles: [{ id: 5, title: "A" }] }],
    [{ tiles: [{ id: "a", title: "" }] }], [{ tiles: [{ type: "APP", id: "a", title: "A" }] }],
    [{ tiles: [{ type: null, id: "a", title: "A" }] }],
    [{ tiles: [{ type: "web", title: "W", url: "ftp://x" }] }],
    [{ tiles: [{ type: "web", title: "W", url: "   " }] }],
    [{ tiles: [{ type: "web", title: "", url: "http://x" }] }],
    [{ tiles: [{ id: "a", title: "A", icon: "" }] }],
    [{ tiles: [{ id: "a", title: "A", icon: 3 }] }],
  ])("rejects bad tile with its index %j", (raw) => {
    expect(() => parseConfig(raw)).toThrow(/posición 0/);
  });

  it("reports the index of the failing tile when it is not the first", () => {
    expect(() => parseConfig({ tiles: [{ id: "a", title: "A" }, { id: "b" }] })).toThrow(/posición 1/);
  });

  it("detects duplicates after trimming and across types", () => {
    expect(() => parseConfig({ tiles: [{ id: "a", title: "A" }, { id: " a ", title: "B" }] })).toThrow(/duplicado: a/);
    expect(() =>
      parseConfig({ tiles: [{ type: "input", id: "a", title: "A" }, { type: "app", id: "a", title: "B" }] })
    ).toThrow(/duplicado: a/);
    expect(() =>
      parseConfig({ tiles: [{ type: "web", title: "A", url: "http://x" }, { type: "web", title: "B", url: "http://x" }] })
    ).toThrow(/duplicado: web:http:\/\/x/);
  });

  it.each([
    [{ veil: -1 }], [{ veil: 101 }], [{ veil: 0.5 }], [{ veil: "50" }], [{ veil: NaN }],
    [{ veil: Infinity }], [{ veil: null }],
  ])("rejects veil %j", (extra) => {
    expect(() => parseConfig({ tiles: [], ...extra })).toThrow(/veil/);
  });

  it("accepts veil boundaries 0 and 100", () => {
    expect(parseConfig({ tiles: [], veil: 0 }).veil).toBe(0);
    expect(parseConfig({ tiles: [], veil: 100 }).veil).toBe(100);
  });

  it.each([
    ["rowStyle", "Names"], ["rowStyle", null], ["clock", 24], ["clock", "13"], ["units", "k"], ["units", "C"],
    ["background", "godrevy"], ["background", "NIGHT"], ["background", ""],
  ])("rejects %s = %j naming the field", (field, value) => {
    expect(() => parseConfig({ tiles: [], [field]: value })).toThrow(new RegExp(field));
  });

  it("accepts every bundled background id", () => {
    BACKGROUND_IDS.forEach((id) => expect(parseConfig({ tiles: [], background: id }).background).toBe(id));
  });

  it.each([
    [{ name: "X", lat: 90.0001, lon: 0 }], [{ name: "X", lat: -91, lon: 0 }], [{ name: "X", lat: 0, lon: 180.5 }],
    [{ name: "X", lat: 0, lon: -181 }], [{ name: "  ", lat: 0, lon: 0 }], [{ name: "X", lat: "1", lon: 0 }],
    [{ name: "X", lat: NaN, lon: 0 }], [{ name: "X", lat: 0, lon: Infinity }], [{ lat: 0, lon: 0 }],
    [{ name: 5, lat: 0, lon: 0 }], ["Madrid"], [[]],
  ])("rejects weather %j", (weather) => {
    expect(() => parseConfig({ tiles: [], weather })).toThrow(/weather/);
  });

  it("accepts weather boundaries and null", () => {
    expect(parseConfig({ tiles: [], weather: { name: "P", lat: -90, lon: 180 } }).weather).toEqual({ name: "P", lat: -90, lon: 180 });
    expect(parseConfig({ tiles: [], weather: null }).weather).toBeNull();
  });

  it("does not let a __proto__ key from JSON leak into the result", () => {
    const raw = JSON.parse('{"tiles":[{"id":"a","title":"A","__proto__":{"icon":"x"}}],"__proto__":{"veil":5}}');
    const cfg = parseConfig(raw);
    expect(cfg.veil).toBe(80);
    expect("icon" in cfg.tiles[0]).toBe(false);
  });

  it("handles 2000 tiles", () => {
    const tiles = Array.from({ length: 2000 }, (_, i) => ({ id: "id" + i, title: "T" + i }));
    expect(parseConfig({ tiles }).tiles.length).toBe(2000);
  });

  it("every thrown error is a ConfigError", () => {
    try {
      parseConfig({ tiles: [], veil: 500 });
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ConfigError);
      expect((e as Error).name).toBe("ConfigError");
    }
  });
});

describe("clock", () => {
  it("12 h maps all 24 hours correctly and never pads the hour", () => {
    const expected = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
    for (let h = 0; h < 24; h++) {
      const out = formatClockWithMode(new Date(2026, 0, 4, h, 7), "12");
      expect(out.time).toBe(`${expected[h]}:07`);
      expect(out.meridiem).toBe(h < 12 ? "AM" : "PM");
    }
  });

  it("date is identical in both modes, including Sunday and a year end", () => {
    const d = new Date(2026, 11, 31, 23, 59);
    expect(formatClockWithMode(d, "12").date).toBe(formatClockWithMode(d, "24").date);
    expect(formatClockWithMode(new Date(2026, 9, 25, 9, 0), "24").date).toBe("domingo, 25 de octubre");
  });
});

describe("launch", () => {
  const okBridge = (log: string[]) => (): ServiceBridge => ({
    onservicecallback: null,
    call(uri: string, payload: string) {
      log.push(payload);
      this.onservicecallback?.('{"returnValue":true}');
    },
  });

  it("escapes special characters in a web target via JSON and sends one call", async () => {
    const log: string[] = [];
    const tile: Tile = { type: "web", id: "web:x", title: "W", url: 'https://x.test/?a="b"&c=\\' };
    await launchTile(tile, okBridge(log));
    expect(log.length).toBe(1);
    expect(JSON.parse(log[0])).toEqual({ id: "com.webos.app.browser", params: { target: tile.url } });
  });

  it("web tile without url rejects LaunchError and never touches the bridge", async () => {
    const factory = vi.fn();
    await expect(launchTile({ type: "web", id: "web:", title: "W" }, factory)).rejects.toMatchObject({
      name: "LaunchError",
      message: "Falta la url",
    });
    expect(factory).not.toHaveBeenCalled();
  });

  it("rejects when the factory throws, on invalid JSON, on returnValue false and settles once", async () => {
    await expect(launchApp("a", () => { throw new LaunchError("x"); })).rejects.toThrow("x");
    const bad: ServiceBridge = { onservicecallback: null, call() { this.onservicecallback?.("not json"); } };
    await expect(launchApp("a", () => bad)).rejects.toThrow("Respuesta inválida");
    const no: ServiceBridge = { onservicecallback: null, call() { this.onservicecallback?.('{"returnValue":false,"errorText":"boom"}'); } };
    await expect(launchApp("a", () => no)).rejects.toThrow("boom");
    const nul: ServiceBridge = { onservicecallback: null, call() { this.onservicecallback?.("null"); } };
    await expect(launchApp("a", () => nul)).rejects.toBeInstanceOf(LaunchError);
  });

  it("rejects with LaunchError (not a throw) when no PalmServiceBridge exists", async () => {
    await expect(launchApp("a")).rejects.toThrow("PalmServiceBridge no disponible");
  });
});

describe("system storage", () => {
  const mem = (value: string | null) => ({ getItem: () => value, setItem: vi.fn() });

  it.each([
    ["0", 3, 0], ["2", 3, 2], ["3", 3, 0], ["-1", 3, 0], ["1.5", 3, 0], ["", 3, 0], [" 1", 3, 0],
    ["1e0", 3, 0], ["NaN", 3, 0], ["99999999999999999999999", 3, 0], [null, 3, 0], ["0", 0, 0],
  ])("loadLastIndex(%j, count %i) = %i", (stored, count, expected) => {
    expect(loadLastIndex(mem(stored as string | null), count)).toBe(expected);
  });

  it("is a no-op for null stores and swallows setItem failures only", () => {
    expect(() => saveLastIndex(null, 1)).not.toThrow();
    expect(() => saveLastIndex({ getItem: () => null, setItem: () => { throw new Error("full"); } }, 1)).not.toThrow();
    expect(loadLastIndex(null, 5)).toBe(0);
  });

  it("getStorage returns null when accessing localStorage throws", () => {
    vi.stubGlobal("window", {
      get localStorage(): never {
        throw new Error("denied");
      },
    });
    expect(getStorage()).toBeNull();
  });
});

describe("weather", () => {
  it.each([
    [0, true, "sun"], [0, false, "moon"], [1, true, "sun"], [2, false, "cloud"], [3, true, "cloud"],
    [4, true, "fog"], [45, true, "fog"], [48, true, "fog"], [49, true, "rain"], [51, true, "rain"],
    [70, true, "rain"], [71, true, "snow"], [77, true, "snow"], [78, true, "rain"], [84, true, "rain"],
    [85, true, "snow"], [86, true, "snow"], [87, true, "rain"], [94, true, "rain"], [95, true, "storm"],
    [99, false, "storm"],
  ])("weatherIcon(%i, %s) = %s", (code, isDay, icon) => {
    expect(weatherIcon(code, isDay)).toBe(icon);
  });

  const place = { name: "P", lat: 1, lon: 2 };
  it.each([
    ['{"current":null}'], ["{}"], ["[]"], ["null"], [""], ['{"current":{"temperature_2m":1,"weather_code":1,"is_day":2}}'],
    ['{"current":{"temperature_2m":1,"weather_code":1,"is_day":true}}'],
    ['{"current":{"temperature_2m":null,"weather_code":1,"is_day":1}}'],
    ['{"current":{"temperature_2m":1,"weather_code":"1","is_day":1}}'],
    ['{"current":{"temperature_2m":1,"is_day":1}}'],
  ])("fetchWeather rejects malformed body %j with Respuesta inválida", async (body) => {
    await expect(fetchWeather(place, "c", () => Promise.resolve(body))).rejects.toMatchObject({
      name: "WeatherError",
      message: "Respuesta inválida",
    });
  });

  it("rounds negatives and wraps non-WeatherError rejections with their message", async () => {
    const ok = '{"current":{"temperature_2m":-3.6,"weather_code":71,"is_day":0}}';
    await expect(fetchWeather(place, "c", () => Promise.resolve(ok))).resolves.toEqual({ temperature: -4, icon: "snow" });
    const err = await fetchWeather(place, "c", () => Promise.reject(new TypeError("boom"))).catch((e) => e);
    expect(err).toBeInstanceOf(WeatherError);
    expect(err.message).toBe("boom");
    const str = await fetchWeather(place, "c", () => Promise.reject("plain")).catch((e) => e);
    expect(str).toBeInstanceOf(WeatherError);
  });

  it("fetchWeather calls httpGet once with the 8000 ms timeout", async () => {
    const get = vi.fn(() => Promise.resolve('{"current":{"temperature_2m":1,"weather_code":1,"is_day":1}}'));
    await fetchWeather(place, "f", get);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0]).toEqual([expect.stringContaining("&temperature_unit=fahrenheit"), 8000]);
  });

  it("xhrGet maps 200, other status, timeout and error", async () => {
    let instance!: { onload: () => void; onerror: () => void; ontimeout: () => void; status: number; responseText: string; timeout: number };
    class X {
      status = 0;
      responseText = "";
      timeout = 0;
      onload!: () => void;
      onerror!: () => void;
      ontimeout!: () => void;
      open = vi.fn();
      send = (): void => {
        instance = this;
      };
    }
    vi.stubGlobal("XMLHttpRequest", X);
    const ok = xhrGet("u", 1234);
    instance.status = 200;
    instance.responseText = "body";
    instance.onload();
    await expect(ok).resolves.toBe("body");
    expect(instance.timeout).toBe(1234);
    const http = xhrGet("u", 1);
    instance.status = 503;
    instance.onload();
    await expect(http).rejects.toMatchObject({ name: "WeatherError", message: "HTTP 503" });
    const to = xhrGet("u", 1);
    instance.ontimeout();
    await expect(to).rejects.toMatchObject({ message: "Tiempo agotado" });
    const er = xhrGet("u", 1);
    instance.onerror();
    await expect(er).rejects.toMatchObject({ message: "Sin conexión" });
  });
});

function model(over: Partial<ViewModel>): ViewModel {
  return {
    tiles: [],
    focusIndex: 0,
    rowStyle: "names",
    clock: { time: "10:00", meridiem: "", date: "x" },
    weather: null,
    background: "night",
    veil: 80,
    error: null,
    ...over,
  };
}

describe("view edge cases", () => {
  const tiles: Tile[] = [
    { type: "app", id: 'a"b', title: "<img src=x onerror=alert(1)>" },
    { type: "app", id: "c", title: "C" },
  ];

  it("never interprets titles, ids, errors or the weather place as HTML", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(model({ tiles, error: "<b>e</b>", weather: { place: "<i>P</i>", now: null } }));
    expect(root.querySelector("img")).toBeNull();
    expect(root.querySelector("b")).toBeNull();
    expect(root.querySelector("i")).toBeNull();
    expect(root.querySelector('[data-tile-id="a\\"b"]')).not.toBeNull();
  });

  it("out-of-range focus index focuses nothing and does not throw", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    expect(() => v.update(model({ tiles, focusIndex: 7 }))).not.toThrow();
    expect(root.querySelectorAll(".is-focused").length).toBe(0);
    expect(() => v.update(model({ tiles, focusIndex: -1 }))).not.toThrow();
  });

  it("exactly one tile has aria-current after many focus changes", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    for (const i of [0, 1, 0, 1, 1]) {
      v.update(model({ tiles, focusIndex: i }));
      expect(root.querySelectorAll('[aria-current="true"]').length).toBe(1);
      expect(root.querySelectorAll(".is-focused").length).toBe(1);
    }
  });

  it("changing only the row style rebuilds the row; weather going from a value back to null removes the element", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(model({ tiles, weather: { place: "M", now: { temperature: 1, icon: "sun" } } }));
    const first = root.querySelector('[data-role="app-row"]');
    v.update(model({ tiles, rowStyle: "tiles", weather: null }));
    expect(root.querySelector('[data-role="app-row"]')).not.toBe(first);
    expect(root.querySelector('[data-role="weather"]')).toBeNull();
    expect(root.querySelectorAll('[data-role="app-initial"]').length).toBe(2);
  });

  it("weather text goes back to the city only when the value disappears", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(model({ tiles, weather: { place: "M", now: { temperature: -0.2 > 0 ? 1 : 0, icon: "sun" } } }));
    expect(root.querySelector('[data-role="weather"]')?.textContent).toContain("M · 0°");
    v.update(model({ tiles, weather: { place: "M", now: null } }));
    expect(root.querySelector('[data-role="weather"]')?.textContent).toBe("M");
    expect(root.querySelector('[data-role="weather-icon"]')).toBeNull();
  });

  it("a second createLauncherView on the same root leaves a single skeleton", () => {
    const root = document.createElement("div");
    createLauncherView(root);
    createLauncherView(root);
    expect(root.querySelectorAll('[data-role="background"]').length).toBe(1);
  });
});

describe("main edge cases", () => {
  const key = (target: EventTarget, code: number): Event => {
    const ev = new KeyboardEvent("keydown", { cancelable: true, bubbles: true });
    Object.defineProperty(ev, "keyCode", { value: code });
    target.dispatchEvent(ev);
    return ev;
  };
  const base = () => ({
    root: document.createElement("div"),
    target: new EventTarget(),
    now: () => new Date(2026, 9, 26, 10, 0),
    platform: null,
    storage: null,
    fetchWeather: vi.fn(() => Promise.resolve({ temperature: 1, icon: "sun" as const })),
  });

  it("an invalid config keeps its error against Down failures, OK and arrows, and weather never runs", async () => {
    vi.useFakeTimers();
    const o = base();
    const launchApp = vi.fn(() => Promise.reject(new Error("x")));
    const h = startLauncher({ ...o, config: {}, launchApp, launchTile: vi.fn() });
    key(o.target, 40);
    await flush();
    key(o.target, 13);
    key(o.target, 39);
    expect(o.root.querySelector('[data-role="error"]')?.textContent).toBe("Configuración inválida");
    expect(o.fetchWeather).not.toHaveBeenCalled();
    h.stop();
  });

  it("a Home failure error is not cleared by Up or Back but is by an arrow", async () => {
    vi.useFakeTimers();
    const o = base();
    const h = startLauncher({
      ...o,
      config: { tiles: [{ id: "a", title: "A" }, { id: "b", title: "B" }] },
      launchApp: () => Promise.reject(new Error("x")),
      platform: { platformBack: vi.fn() },
    });
    key(o.target, 40);
    await flush();
    expect(o.root.querySelector('[data-role="error"]')?.textContent).toBe("No se pudo abrir el Home de LG");
    key(o.target, 38);
    key(o.target, 461);
    expect(o.root.querySelector('[data-role="error"]')).not.toBeNull();
    key(o.target, 39);
    expect(o.root.querySelector('[data-role="error"]')).toBeNull();
    h.stop();
  });

  it("OK and Down share the in-flight guard and the guard is released after a rejection", async () => {
    vi.useFakeTimers();
    const o = base();
    let rejectOpen: (e: Error) => void = () => undefined;
    const launchTile = vi.fn(() => new Promise<void>((_, rej) => { rejectOpen = rej; }));
    const launchApp = vi.fn(() => Promise.resolve());
    const h = startLauncher({ ...o, config: { tiles: [{ id: "a", title: "A" }] }, launchTile, launchApp });
    key(o.target, 13);
    key(o.target, 40);
    key(o.target, 13);
    expect(launchTile).toHaveBeenCalledTimes(1);
    expect(launchApp).not.toHaveBeenCalled();
    rejectOpen(new Error("x"));
    await flush();
    key(o.target, 40);
    expect(launchApp).toHaveBeenCalledTimes(1);
    h.stop();
  });

  it("OK with no tiles does nothing and does not store an index", () => {
    vi.useFakeTimers();
    const o = base();
    const store = { getItem: vi.fn(() => null), setItem: vi.fn() };
    const launchTile = vi.fn();
    const h = startLauncher({ ...o, config: { tiles: [] }, storage: store, launchTile });
    expect(key(o.target, 13).defaultPrevented).toBe(true);
    expect(launchTile).not.toHaveBeenCalled();
    expect(store.setItem).not.toHaveBeenCalled();
    h.stop();
  });

  it("a throwing storage does not break start or OK", () => {
    vi.useFakeTimers();
    const o = base();
    const store = { getItem: () => { throw new Error("x"); }, setItem: () => { throw new Error("y"); } };
    const launchTile = vi.fn(() => Promise.resolve());
    const h = startLauncher({ ...o, config: { tiles: [{ id: "a", title: "A" }] }, storage: store, launchTile });
    expect(() => key(o.target, 13)).not.toThrow();
    expect(launchTile).toHaveBeenCalledTimes(1);
    h.stop();
  });

  it("weather late result after stop() does not render; refresh keeps the previous value after failure", async () => {
    vi.useFakeTimers();
    const o = base();
    let resolveWeather: (w: { temperature: number; icon: "sun" }) => void = () => undefined;
    const fetchWeather = vi.fn(() => new Promise<{ temperature: number; icon: "sun" }>((r) => { resolveWeather = r; }));
    const nowSpy = vi.fn(o.now);
    const h = startLauncher({
      ...o,
      now: nowSpy,
      fetchWeather,
      config: { tiles: [], weather: { name: "M", lat: 1, lon: 2 } },
    });
    h.stop();
    const calls = nowSpy.mock.calls.length;
    resolveWeather({ temperature: 5, icon: "sun" });
    await flush();
    expect(nowSpy.mock.calls.length).toBe(calls);
    vi.advanceTimersByTime(WEATHER_REFRESH_MS * 2);
    expect(fetchWeather).toHaveBeenCalledTimes(1);
  });

  it("the weather uses the configured units on every refresh and the clock ticks every second", async () => {
    vi.useFakeTimers();
    const o = base();
    const nowSpy = vi.fn(o.now);
    const h = startLauncher({
      ...o,
      now: nowSpy,
      config: { tiles: [], units: "f", weather: { name: "M", lat: 1, lon: 2 } },
    });
    const afterStart = nowSpy.mock.calls.length;
    vi.advanceTimersByTime(3000);
    expect(nowSpy.mock.calls.length).toBe(afterStart + 3);
    vi.advanceTimersByTime(WEATHER_REFRESH_MS);
    expect(o.fetchWeather.mock.calls.every((c) => (c as unknown[])[1] === "f")).toBe(true);
    h.stop();
  });

  it("rethrows non-ConfigError failures instead of hiding them", () => {
    const o = base();
    const config = new Proxy({}, { get: () => { throw new RangeError("boom"); } });
    expect(() => startLauncher({ ...o, config })).toThrow(RangeError);
  });

  it("clock 12 renders meridiem in the DOM", () => {
    vi.useFakeTimers();
    const o = base();
    const h = startLauncher({ ...o, config: { tiles: [], clock: "12" }, now: () => new Date(2026, 9, 26, 19, 5) });
    expect(o.root.querySelector('[data-role="clock-time"]')?.textContent).toBe("7:05");
    expect(o.root.querySelector('[data-role="clock-meridiem"]')?.textContent).toBe("PM");
    h.stop();
  });
});

describe("conventions and static review (architecture sections 4b, 7, 9)", () => {
  const read = (...p: string[]): string => fs.readFileSync(path.join(...p), "utf8");
  const runtime = fs.readdirSync(SRC).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
  const css = read(PUBLIC, "style.css");

  it("style.css has no flex gap and no inset (Chromium 53)", () => {
    expect(css).not.toMatch(/(^|[;{\s])(row-|column-)?gap\s*:/m);
    expect(css).not.toMatch(/(^|[;{\s])inset\s*:/m);
  });

  it("style.css transitions only transform and opacity", () => {
    const transitions = css.match(/transition\s*:[^;]+;/g) ?? [];
    expect(transitions.length).toBeGreaterThan(0);
    transitions.forEach((t) => expect(t).toMatch(/^transition\s*:\s*(transform|opacity)\b/));
  });

  it("style.css declares the four Jost faces and credits Lightkeeper", () => {
    expect(css).toContain("assets/fonts/jost-200.woff2");
    expect(css).toContain("assets/fonts/jost-300.woff2");
    expect(css).toContain("assets/fonts/jost-500.woff2");
    expect(css).toContain("assets/fonts/jost.woff2");
    expect(css).toMatch(/Lightkeeper \(MIT/);
    expect(css).toContain("#ff6b6b");
  });

  it("every font and background referenced by the CSS and the types exists, and CREDITS lists every background", () => {
    const credits = read(PUBLIC, "assets", "CREDITS.md");
    BACKGROUND_IDS.forEach((id) => {
      expect(fs.existsSync(path.join(PUBLIC, "assets", "backgrounds", id + ".jpg"))).toBe(true);
      expect(credits).toContain(id + ".jpg");
    });
    expect(fs.existsSync(path.join(PUBLIC, "assets", "backgrounds", "godrevy.jpg"))).toBe(false);
    expect(credits).toMatch(/Open-Meteo/);
    expect(credits).toMatch(/OFL|Open Font License/);
  });

  it("runtime code has no async/await, fetch, padStart, Object.values/entries, .includes(", () => {
    runtime.forEach((f) => {
      const code = read(SRC, f);
      expect(code, f).not.toMatch(/\basync\b|\bawait\b/);
      expect(code, f).not.toMatch(/\bfetch\s*\(/);
      expect(code, f).not.toMatch(/padStart|Object\.values|Object\.entries|\.includes\(/);
    });
  });

  it("innerHTML only appears in view.ts and only with WEATHER_ICON_SVG", () => {
    runtime.forEach((f) => {
      const code = read(SRC, f);
      if (f !== "view.ts" && f !== "weather-icons.ts") expect(code, f).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
    });
    const view = read(SRC, "view.ts");
    const uses = view.match(/innerHTML[^\n]*/g) ?? [];
    expect(uses.length).toBe(1);
    expect(uses[0]).toContain("WEATHER_ICON_SVG");
    expect(view).not.toMatch(/from "\.\/weather"/);
  });

  it("localStorage is only touched in system.ts and XMLHttpRequest only in weather.ts", () => {
    runtime.forEach((f) => {
      const code = read(SRC, f);
      if (f !== "system.ts") expect(code, f).not.toMatch(/localStorage/);
      if (f !== "weather.ts") expect(code, f).not.toMatch(/XMLHttpRequest/);
    });
  });

  it("appinfo.json meets the v2 contract and index.html is untouched in shape", () => {
    const info = JSON.parse(read(PUBLIC, "appinfo.json"));
    expect(info).toMatchObject({
      id: "com.jagosar.minimal.launcher",
      version: "2.0.0",
      bgColor: "#000000",
      disableBackHistoryAPI: true,
    });
    const html = read(PUBLIC, "index.html");
    expect(html).toContain('<div id="app">');
    expect(html).not.toContain('type="module"');
  });

  it("v1 files are gone and the bundled config.json parses", () => {
    expect(fs.existsSync(path.join(SRC, "apps.ts"))).toBe(false);
    expect(fs.existsSync(path.join(SRC, "apps.json"))).toBe(false);
    expect(parseConfig(JSON.parse(read(SRC, "config.json"))).tiles.length).toBe(6);
  });

  it("weather icon SVG constants are complete, static and cover every icon", async () => {
    const { WEATHER_ICON_SVG } = await import("../src/weather-icons");
    const icons = ["sun", "moon", "cloud", "fog", "rain", "snow", "storm"] as const;
    icons.forEach((i) => {
      expect(WEATHER_ICON_SVG[i]).toMatch(/^<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"[^>]*>.*<\/svg>$/);
      expect(WEATHER_ICON_SVG[i]).not.toMatch(/<script|on\w+=/i);
    });
  });
});
