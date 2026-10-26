import { describe, it, expect } from "vitest";
import { createLauncherView, computeRowOffset, ROW_VIEWPORT_PX } from "../src/view";
import type { Tile, ViewModel } from "../src/types";

function makeTiles(): Tile[] {
  const tiles: Tile[] = [];
  for (let i = 0; i < 8; i++) {
    tiles.push({ type: "app", id: `a${i}`, title: `App ${i}` });
  }
  return tiles;
}

const TILES = makeTiles();

function baseModel(): ViewModel {
  return {
    tiles: TILES,
    focusIndex: 0,
    rowStyle: "names",
    clock: { time: "7:05", meridiem: "PM", date: "lunes, 26 de octubre" },
    weather: { place: "Madrid", now: { temperature: 18, icon: "cloud" } },
    background: "monk",
    veil: 60,
    error: null,
  };
}

function q(root: HTMLElement, role: string): HTMLElement | null {
  return root.querySelector(`[data-role="${role}"]`);
}

function stubMeasurements(root: HTMLElement): void {
  const tiles = root.querySelectorAll('[data-role="app-tile"]');
  tiles.forEach((el, i) => {
    Object.defineProperty(el, "offsetLeft", { value: i * 300, configurable: true });
    Object.defineProperty(el, "offsetWidth", { value: 250, configurable: true });
  });
}

describe("AC-016 · View: styles, background, weather and the sliding row", () => {
  it("test_ac_016_computeRowOffset_rule", () => {
    expect(ROW_VIEWPORT_PX).toBe(1696);
    expect(computeRowOffset(2100, 250, 1696, 0)).toBe(654);
    expect(computeRowOffset(100, 250, 1696, 500)).toBe(100);
    expect(computeRowOffset(100, 250, 1696, 0)).toBe(0);
  });

  it("test_ac_016_renders_background_veil_clock_weather_and_tiles", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(baseModel());

    expect(q(root, "background")!.style.backgroundImage).toContain("assets/backgrounds/monk.jpg");
    expect(q(root, "veil")!.style.opacity).toBe("0.6");
    expect(q(root, "clock-time")!.textContent).toBe("7:05");
    expect(q(root, "clock-meridiem")!.textContent).toBe("PM");
    expect(q(root, "clock-date")!.textContent).toBe("lunes, 26 de octubre");

    const weather = q(root, "weather")!;
    expect(weather.textContent!.indexOf("Madrid · 18°")).toBe(0);
    const icon = weather.querySelector('[data-role="weather-icon"][data-icon="cloud"]');
    expect(icon).not.toBeNull();
    expect(icon!.querySelector("svg")).not.toBeNull();

    const tiles = root.querySelectorAll('[data-role="app-tile"]');
    expect(tiles).toHaveLength(8);
    tiles.forEach((el, i) => {
      expect(el.getAttribute("data-tile-id")).toBe(`a${i}`);
      expect(el.getAttribute("data-tile-type")).toBe("app");
      expect(el.querySelector('[data-role="app-title"]')).not.toBeNull();
      expect(el.classList.contains("is-focused")).toBe(i === 0);
      expect(el.getAttribute("aria-current")).toBe(i === 0 ? "true" : null);
    });
    expect(q(root, "app-row")!.getAttribute("data-style")).toBe("names");
    expect(root.querySelector('[data-role="app-initial"]')).toBeNull();
    expect(root.querySelector('[data-role="app-icon"]')).toBeNull();
  });

  it("test_ac_016_meridiem_present_but_empty_in_24h", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update({ ...baseModel(), clock: { time: "19:05", meridiem: "", date: "x" } });
    expect(q(root, "clock-meridiem")).not.toBeNull();
    expect(q(root, "clock-meridiem")!.textContent).toBe("");
  });

  it("test_ac_016_weather_without_now_shows_only_place", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update({ ...baseModel(), weather: { place: "Madrid", now: null } });
    expect(q(root, "weather")!.textContent!.indexOf("Madrid")).toBe(0);
    expect(root.querySelector('[data-role="weather-icon"]')).toBeNull();
  });

  it("test_ac_016_row_slides_and_is_not_rebuilt_on_focus_change", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(baseModel());
    stubMeasurements(root);
    const row = q(root, "app-row");
    expect(v.rowOffset()).toBe(0);

    v.update({ ...baseModel(), focusIndex: 7 });
    expect(q(root, "app-row")).toBe(row);
    expect(v.rowOffset()).toBe(7 * 300 + 250 - 1696);
    expect(v.rowOffset()).toBe(654);
    expect(row!.style.transform).toBe("translateX(-654px)");
    const focused = root.querySelectorAll(".is-focused");
    expect(focused).toHaveLength(1);
    expect(focused[0].getAttribute("data-tile-id")).toBe("a7");

    v.update({ ...baseModel(), focusIndex: 0 });
    expect(q(root, "app-row")).toBe(row);
    expect(v.rowOffset()).toBe(0);
  });

  it("test_ac_016_tiles_style_rebuilds_and_shows_icon", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(baseModel());
    const row = q(root, "app-row");
    const tiles: Tile[] = [{ type: "app", id: "b", title: "beta", icon: "b.png" }];
    v.update({ ...baseModel(), tiles, rowStyle: "tiles" });
    expect(q(root, "app-row")).not.toBe(row);
    expect(q(root, "app-row")!.getAttribute("data-style")).toBe("tiles");
    const img = root.querySelector('img[data-role="app-icon"]') as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute("src")!.slice(-5)).toBe("b.png");
  });

  it("test_ac_016_tiles_style_without_icon_shows_initial", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    const tiles: Tile[] = [{ type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" }];
    v.update({ ...baseModel(), tiles, rowStyle: "tiles" });
    expect(root.querySelector('[data-role="app-initial"]')).not.toBeNull();
  });

  it("test_ac_016_no_weather_error_and_empty_state", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    v.update(baseModel());
    v.update({ ...baseModel(), weather: null, error: "Ups", tiles: [] });
    expect(q(root, "weather")).toBeNull();
    expect(q(root, "error")!.textContent).toBe("Ups");
    expect(q(root, "empty")!.textContent).toBe("No hay apps configuradas");
    expect(root.querySelectorAll('[data-role="app-tile"]')).toHaveLength(0);
    expect(q(root, "app-row")).toBeNull();

    v.update({ ...baseModel(), tiles: [] });
    expect(q(root, "error")).toBeNull();
  });

  it("test_ac_016_config_text_is_never_interpreted_as_html", () => {
    const root = document.createElement("div");
    const v = createLauncherView(root);
    const tiles: Tile[] = [{ type: "app", id: "x", title: "<b>x</b>" }];
    v.update({ ...baseModel(), tiles, weather: { place: "<i>M</i>", now: null }, error: "<u>e</u>" });
    expect(root.querySelector("b")).toBeNull();
    expect(root.querySelector("i")).toBeNull();
    expect(root.querySelector("u")).toBeNull();
    expect(root.querySelector('[data-role="app-title"]')!.textContent).toBe("<b>x</b>");
  });
});
