import { describe, it, expect } from "vitest";
import { computeRowOffset, createLauncherView, ROW_VIEWPORT_PX } from "./view";
import type { Tile, ViewModel } from "./types";

const TILES: Tile[] = [
  { type: "app", id: "a", title: "alpha" },
  { type: "app", id: "b", title: "Beta", icon: "b.png" },
  { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" },
];

function model(overrides: Partial<ViewModel> = {}): ViewModel {
  return {
    tiles: TILES,
    focusIndex: 0,
    rowStyle: "names",
    clock: { time: "7:05", meridiem: "PM", date: "lunes, 26 de octubre" },
    weather: { place: "Madrid", now: { temperature: 18, icon: "cloud" } },
    background: "monk",
    veil: 60,
    error: null,
    ...overrides,
  };
}

function role(root: HTMLElement, name: string): HTMLElement | null {
  return root.querySelector('[data-role="' + name + '"]');
}

function stubTiles(root: HTMLElement): void {
  root.querySelectorAll('[data-role="app-tile"]').forEach((el, i) => {
    Object.defineProperty(el, "offsetLeft", { value: i * 300, configurable: true });
    Object.defineProperty(el, "offsetWidth", { value: 250, configurable: true });
  });
}

describe("computeRowOffset", () => {
  it("scrolls_right_when_the_item_overflows", () => {
    expect(computeRowOffset(2100, 250, ROW_VIEWPORT_PX, 0)).toBe(654);
  });

  it("scrolls_left_when_the_item_is_before_the_offset", () => {
    expect(computeRowOffset(100, 250, ROW_VIEWPORT_PX, 500)).toBe(100);
  });

  it("keeps_the_offset_when_the_item_is_visible", () => {
    expect(computeRowOffset(100, 250, ROW_VIEWPORT_PX, 0)).toBe(0);
  });
});

describe("createLauncherView", () => {
  it("renders_background_veil_clock_and_weather", () => {
    const root = document.createElement("div");
    createLauncherView(root).update(model());
    expect(role(root, "background")!.style.backgroundImage).toContain("assets/backgrounds/monk.jpg");
    expect(role(root, "veil")!.style.opacity).toBe("0.6");
    expect(role(root, "clock-time")!.textContent).toBe("7:05");
    expect(role(root, "clock-meridiem")!.textContent).toBe("PM");
    expect(role(root, "weather")!.textContent!.indexOf("Madrid · 18°")).toBe(0);
    expect(root.querySelector('[data-role="weather-icon"][data-icon="cloud"] svg')).not.toBeNull();
  });

  it("omits_weather_when_null_and_icon_when_no_data", () => {
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model({ weather: { place: "Madrid", now: null } }));
    expect(role(root, "weather")!.textContent).toBe("Madrid");
    expect(role(root, "weather-icon")).toBeNull();
    view.update(model({ weather: null }));
    expect(role(root, "weather")).toBeNull();
  });

  it("marks_only_the_focused_tile", () => {
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model({ focusIndex: 1 }));
    expect(root.querySelectorAll(".is-focused")).toHaveLength(1);
    expect(root.querySelector('[data-tile-id="b"]')!.getAttribute("aria-current")).toBe("true");
    view.update(model({ focusIndex: 2 }));
    expect(root.querySelector('[data-tile-id="b"]')!.getAttribute("aria-current")).toBeNull();
  });

  it("names_style_has_no_icon_or_initial_and_tiles_style_has_both", () => {
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model());
    expect(role(root, "app-initial")).toBeNull();
    view.update(model({ rowStyle: "tiles" }));
    expect(role(root, "app-row")!.getAttribute("data-style")).toBe("tiles");
    expect(root.querySelector('[data-tile-id="a"] [data-role="app-initial"]')!.textContent).toBe("A");
    expect(root.querySelector('img[data-role="app-icon"]')!.getAttribute("src")).toBe("b.png");
    expect(root.querySelector('[data-tile-id="com.webos.app.hdmi1"]')!.getAttribute("data-tile-type")).toBe("input");
  });

  it("slides_the_row_without_rebuilding_it", () => {
    const tiles: Tile[] = [];
    for (let i = 0; i < 8; i++) tiles.push({ type: "app", id: "a" + i, title: "App " + i });
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model({ tiles }));
    stubTiles(root);
    const row = role(root, "app-row");

    view.update(model({ tiles, focusIndex: 7 }));
    expect(role(root, "app-row")).toBe(row);
    expect(view.rowOffset()).toBe(654);
    expect(row!.style.transform).toBe("translateX(-654px)");

    view.update(model({ tiles, focusIndex: 0 }));
    expect(view.rowOffset()).toBe(0);
    expect(role(root, "app-row")).toBe(row);
  });

  it("rebuilds_the_row_when_tiles_change", () => {
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model());
    const row = role(root, "app-row");
    view.update(model({ tiles: [...TILES] }));
    expect(role(root, "app-row")).not.toBe(row);
  });

  it("shows_empty_message_and_error_without_tiles", () => {
    const root = document.createElement("div");
    const view = createLauncherView(root);
    view.update(model({ tiles: [], error: "Ups" }));
    expect(role(root, "app-row")).toBeNull();
    expect(role(root, "empty")!.textContent).toBe("No hay apps configuradas");
    expect(role(root, "error")!.textContent).toBe("Ups");
    view.update(model({ tiles: [] }));
    expect(role(root, "error")).toBeNull();
    view.update(model());
    expect(role(root, "empty")).toBeNull();
    expect(role(root, "app-row")).not.toBeNull();
  });

  it("sets_text_without_interpreting_html", () => {
    const root = document.createElement("div");
    const tiles: Tile[] = [{ type: "app", id: "x", title: "<b>x</b>" }];
    createLauncherView(root).update(
      model({ tiles, weather: { place: "<i>M</i>", now: null }, error: "<u>e</u>" })
    );
    expect(root.querySelector("b")).toBeNull();
    expect(root.querySelector("i")).toBeNull();
    expect(root.querySelector("u")).toBeNull();
  });
});
