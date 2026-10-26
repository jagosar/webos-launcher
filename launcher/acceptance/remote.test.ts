import { describe, it, expect, vi, afterEach } from "vitest";
import { startLauncher } from "../src/main";
import type { LauncherHandle } from "../src/main";
import type { KeyValueStore, Tile, WeatherNow, WeatherPlace, Units } from "../src/types";

let handles: LauncherHandle[] = [];

afterEach(() => {
  handles.forEach((h) => h.stop());
  handles = [];
  vi.useRealTimers();
});

function track(h: LauncherHandle): LauncherHandle {
  handles.push(h);
  return h;
}

function press(target: EventTarget, keyCode: number): KeyboardEvent {
  const ev = new KeyboardEvent("keydown", { keyCode, cancelable: true, bubbles: true });
  Object.defineProperty(ev, "keyCode", { value: keyCode });
  target.dispatchEvent(ev);
  return ev;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 20; i++) {
    await Promise.resolve();
  }
}

function freshRoot(): HTMLElement {
  const root = document.createElement("div");
  document.body.appendChild(root);
  return root;
}

function focusedId(root: HTMLElement): string | null {
  const el = root.querySelector(".is-focused");
  return el ? el.getAttribute("data-tile-id") : null;
}

function errorText(root: HTMLElement): string | null {
  const el = root.querySelector('[data-role="error"]');
  return el ? el.textContent : null;
}

function weatherText(root: HTMLElement): string | null {
  const el = root.querySelector('[data-role="weather"]');
  return el ? el.textContent : null;
}

const FIXED_NOW = (): Date => new Date(2026, 9, 26, 7, 5);

describe("AC-017 · Remote control end to end", () => {
  it("test_ac_017_keys_drive_focus_launch_home_and_back", async () => {
    document.body.innerHTML = '<div id="app"></div>';
    const root = document.getElementById("app") as HTMLElement;
    const data: { [k: string]: string } = { "launcher.lastIndex": "1" };
    const storage: KeyValueStore = {
      getItem: (k) => (k in data ? data[k] : null),
      setItem: (k, v) => {
        data[k] = v;
      },
    };
    const launchTile = vi.fn((_t: Tile) => Promise.resolve());
    const launchApp = vi.fn((_id: string) => Promise.resolve());
    const platformBack = vi.fn();
    const fetchWeather = vi.fn((_p: WeatherPlace, _u: Units) => Promise.resolve<WeatherNow>({ temperature: 1, icon: "sun" }));

    track(
      startLauncher({
        root,
        config: {
          tiles: [
            { id: "a", title: "A" },
            { type: "web", title: "W", url: "https://w.test" },
            { id: "c", title: "C" },
          ],
        },
        now: FIXED_NOW,
        launchTile,
        launchApp,
        platform: { platformBack },
        storage,
        fetchWeather,
      }),
    );

    expect(focusedId(root)).toBe("web:https://w.test");

    const right = press(document, 39);
    expect(right.defaultPrevented).toBe(true);
    expect(focusedId(root)).toBe("c");
    expect(launchTile).not.toHaveBeenCalled();

    const ok = press(document, 13);
    expect(ok.defaultPrevented).toBe(true);
    await flush();
    expect(launchTile).toHaveBeenCalledTimes(1);
    expect(launchTile.mock.calls[0][0]).toEqual({ type: "app", id: "c", title: "C" });
    expect(data["launcher.lastIndex"]).toBe("2");

    const down = press(document, 40);
    expect(down.defaultPrevented).toBe(true);
    await flush();
    expect(launchApp).toHaveBeenCalledTimes(1);
    expect(launchApp.mock.calls[0][0]).toBe("com.webos.app.home");

    const up = press(document, 38);
    expect(up.defaultPrevented).toBe(true);
    expect(launchTile).toHaveBeenCalledTimes(1);
    expect(launchApp).toHaveBeenCalledTimes(1);
    expect(platformBack).not.toHaveBeenCalled();

    const back = press(document, 461);
    expect(back.defaultPrevented).toBe(true);
    expect(platformBack).toHaveBeenCalledTimes(1);

    const other = press(document, 65);
    expect(other.defaultPrevented).toBe(false);
    expect(platformBack).toHaveBeenCalledTimes(1);
    expect(launchTile).toHaveBeenCalledTimes(1);
    expect(launchApp).toHaveBeenCalledTimes(1);
    expect(fetchWeather).not.toHaveBeenCalled();
  });
});

describe("AC-018 · Errors, weather refresh and stopping", () => {
  it("test_ac_018_weather_refresh_errors_config_back_and_stop", async () => {
    vi.useFakeTimers();
    const config = { tiles: [{ id: "a", title: "A" }], weather: { name: "Madrid", lat: 1, lon: 2 } };

    // Launcher 1: weather refresh and a rejected launch.
    const root1 = freshRoot();
    const target1 = new EventTarget();
    const now1 = vi.fn(FIXED_NOW);
    const fetch1 = vi
      .fn<(p: WeatherPlace, u: Units) => Promise<WeatherNow>>()
      .mockResolvedValueOnce({ temperature: 20, icon: "sun" })
      .mockRejectedValue(new Error("down"));
    const launch1 = vi.fn((_t: Tile) => Promise.reject(new Error("nope")));
    handles.push(
      startLauncher({ root: root1, config, now: now1, target: target1, storage: null, launchTile: launch1, fetchWeather: fetch1 }),
    );
    await flush();
    expect(fetch1).toHaveBeenCalledTimes(1);
    expect(fetch1.mock.calls[0][0]).toEqual({ name: "Madrid", lat: 1, lon: 2 });
    expect(fetch1.mock.calls[0][1]).toBe("c");
    expect(weatherText(root1)!.indexOf("Madrid · 20°")).toBe(0);

    vi.advanceTimersByTime(1800000);
    await flush();
    expect(fetch1).toHaveBeenCalledTimes(2);
    expect(weatherText(root1)!.indexOf("Madrid · 20°")).toBe(0);
    expect(errorText(root1)).toBeNull();

    press(target1, 13);
    await flush();
    expect(errorText(root1)).toBe("No se pudo abrir A");
    press(target1, 39);
    expect(errorText(root1)).toBeNull();

    // Launcher 2: invalid config.
    const root2 = freshRoot();
    const target2 = new EventTarget();
    const now2 = vi.fn(FIXED_NOW);
    const fetch2 = vi.fn((_p: WeatherPlace, _u: Units) => Promise.resolve<WeatherNow>({ temperature: 1, icon: "sun" }));
    expect(() => {
      handles.push(startLauncher({ root: root2, config: {}, now: now2, target: target2, storage: null, fetchWeather: fetch2 }));
    }).not.toThrow();
    expect(errorText(root2)).toBe("Configuración inválida");
    expect(root2.querySelectorAll('[data-role="app-tile"]')).toHaveLength(0);
    press(target2, 39);
    expect(errorText(root2)).toBe("Configuración inválida");

    // Launcher 3: no platform, Back must not throw.
    const root3 = freshRoot();
    const target3 = new EventTarget();
    const now3 = vi.fn(FIXED_NOW);
    const fetch3 = vi.fn((_p: WeatherPlace, _u: Units) => Promise.resolve<WeatherNow>({ temperature: 1, icon: "sun" }));
    handles.push(startLauncher({ root: root3, config, now: now3, target: target3, storage: null, platform: null, fetchWeather: fetch3 }));
    let back: KeyboardEvent | null = null;
    expect(() => {
      back = press(target3, 461);
    }).not.toThrow();
    expect(back!.defaultPrevented).toBe(true);

    await flush();
    handles.forEach((h) => h.stop());
    const counts = [fetch1, fetch2, fetch3, now1, now2, now3].map((s) => s.mock.calls.length);
    vi.advanceTimersByTime(3600000);
    await flush();
    expect([fetch1, fetch2, fetch3, now1, now2, now3].map((s) => s.mock.calls.length)).toEqual(counts);
  });

  it("test_ac_018_keys_after_stop_have_no_effect", async () => {
    const root = freshRoot();
    const target = new EventTarget();
    const launch = vi.fn((_t: Tile) => Promise.resolve());
    const h = startLauncher({
      root,
      config: { tiles: [{ id: "a", title: "A" }] },
      now: FIXED_NOW,
      target,
      storage: null,
      launchTile: launch,
    });
    h.stop();
    press(target, 13);
    await flush();
    expect(launch).not.toHaveBeenCalled();
  });
});
