import { describe, it, expect, vi, afterEach } from "vitest";
import { startLauncher, CLOCK_TICK_MS, WEATHER_REFRESH_MS } from "./main";
import type { LauncherHandle, LauncherOptions } from "./main";
import type { KeyValueStore } from "./types";

const CONFIG = {
  tiles: [
    { id: "a", title: "A" },
    { id: "b", title: "B" },
  ],
};
const FIXED_NOW = (): Date => new Date(2026, 9, 26, 7, 5);

let handle: LauncherHandle | null = null;

function setup(options: Partial<LauncherOptions> = {}): { root: HTMLElement } {
  document.body.innerHTML = '<div id="app"></div>';
  const root = document.getElementById("app") as HTMLElement;
  handle = startLauncher({
    root,
    config: CONFIG,
    now: FIXED_NOW,
    launchTile: () => Promise.resolve(),
    launchApp: () => Promise.resolve(),
    platform: null,
    storage: null,
    ...options,
  });
  return { root };
}

function press(keyCode: number): KeyboardEvent {
  const ev = new KeyboardEvent("keydown", { cancelable: true, bubbles: true });
  Object.defineProperty(ev, "keyCode", { value: keyCode });
  document.dispatchEvent(ev);
  return ev;
}

const focusedId = (root: HTMLElement): string | null => {
  const tile = root.querySelector(".is-focused");
  return tile ? tile.getAttribute("data-tile-id") : null;
};

const errorText = (root: HTMLElement): string | null => {
  const el = root.querySelector('[data-role="error"]');
  return el ? el.textContent : null;
};

const settle = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  if (handle) handle.stop();
  handle = null;
  vi.useRealTimers();
});

describe("startLauncher", () => {
  it("renders the clock and tiles immediately", () => {
    const { root } = setup();
    expect(root.querySelectorAll('[data-role="app-tile"]').length).toBe(2);
    expect(root.querySelector('[data-role="clock-time"]')?.textContent).toBe("07:05");
  });

  it("focuses the remembered tile", () => {
    const storage: KeyValueStore = { getItem: () => "1", setItem: () => undefined };
    const { root } = setup({ storage });
    expect(focusedId(root)).toBe("b");
  });

  it("moves focus on right without wrapping or launching", () => {
    const launchTile = vi.fn(() => Promise.resolve());
    const { root } = setup({ launchTile });
    expect(press(39).defaultPrevented).toBe(true);
    press(39);
    expect(focusedId(root)).toBe("b");
    expect(launchTile).not.toHaveBeenCalled();
  });

  it("ignores unknown keys without preventDefault", () => {
    setup();
    expect(press(65).defaultPrevented).toBe(false);
  });

  it("launches the focused tile on OK and saves its index", () => {
    const launchTile = vi.fn(() => Promise.resolve());
    const setItem = vi.fn();
    setup({ launchTile, storage: { getItem: () => null, setItem } });
    press(39);
    press(13);
    expect(launchTile).toHaveBeenCalledWith({ type: "app", id: "b", title: "B" });
    expect(setItem).toHaveBeenCalledWith("launcher.lastIndex", "1");
  });

  it("ignores OK while a launch is in flight", async () => {
    let resolveLaunch: () => void = () => undefined;
    const launchTile = vi.fn(() => new Promise<void>((r) => { resolveLaunch = r; }));
    setup({ launchTile });
    press(13);
    press(13);
    expect(launchTile).toHaveBeenCalledTimes(1);
    resolveLaunch();
    await settle();
    press(13);
    expect(launchTile).toHaveBeenCalledTimes(2);
  });

  it("opens LG Home on Down and reports failure", async () => {
    const launchApp = vi.fn(() => Promise.reject(new Error("x")));
    const { root } = setup({ launchApp });
    press(40);
    await settle();
    expect(launchApp).toHaveBeenCalledWith("com.webos.app.home");
    expect(errorText(root)).toBe("No se pudo abrir el Home de LG");
  });

  it("shows a launch error and clears it on arrow", async () => {
    const { root } = setup({ launchTile: () => Promise.reject(new Error("x")) });
    press(13);
    await settle();
    expect(errorText(root)).toBe("No se pudo abrir A");
    press(39);
    expect(errorText(root)).toBeNull();
  });

  it("keeps the invalid config error", () => {
    const { root } = setup({ config: {} });
    expect(root.querySelectorAll('[data-role="app-tile"]').length).toBe(0);
    press(39);
    expect(errorText(root)).toBe("Configuración inválida");
  });

  it("calls platformBack on Back only", () => {
    const platformBack = vi.fn();
    setup({ platform: { platformBack } });
    press(38);
    expect(platformBack).not.toHaveBeenCalled();
    expect(press(461).defaultPrevented).toBe(true);
    expect(platformBack).toHaveBeenCalledTimes(1);
  });

  it("does not throw on Back without platform", () => {
    setup({ platform: null });
    expect(press(461).defaultPrevented).toBe(true);
  });

  it("re-renders the clock every tick", () => {
    vi.useFakeTimers();
    const now = vi.fn(FIXED_NOW);
    setup({ now });
    const before = now.mock.calls.length;
    vi.advanceTimersByTime(CLOCK_TICK_MS * 3);
    expect(now.mock.calls.length).toBe(before + 3);
  });

  it("fetches weather at start and on refresh, keeping the last value on failure", async () => {
    vi.useFakeTimers();
    const fetchWeather = vi
      .fn()
      .mockResolvedValueOnce({ temperature: 20, icon: "sun" })
      .mockRejectedValue(new Error("x"));
    const { root } = setup({
      config: { ...CONFIG, weather: { name: "Madrid", lat: 1, lon: 2 } },
      fetchWeather,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchWeather).toHaveBeenCalledWith({ name: "Madrid", lat: 1, lon: 2 }, "c");
    expect(root.querySelector('[data-role="weather"]')?.textContent).toContain("Madrid · 20°");
    await vi.advanceTimersByTimeAsync(WEATHER_REFRESH_MS);
    expect(fetchWeather).toHaveBeenCalledTimes(2);
    expect(root.querySelector('[data-role="weather"]')?.textContent).toContain("Madrid · 20°");
    expect(errorText(root)).toBeNull();
  });

  it("stop removes the listener and the timers", () => {
    vi.useFakeTimers();
    const launchTile = vi.fn(() => Promise.resolve());
    const now = vi.fn(FIXED_NOW);
    setup({ launchTile, now });
    handle?.stop();
    const calls = now.mock.calls.length;
    press(13);
    vi.advanceTimersByTime(5000);
    expect(launchTile).not.toHaveBeenCalled();
    expect(now.mock.calls.length).toBe(calls);
  });
});
