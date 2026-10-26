import { describe, it, expect } from "vitest";
import { parseConfig, ConfigError } from "../src/config";
import defaultConfig from "../src/config.json";

function messageOf(raw: unknown): string {
  try {
    parseConfig(raw);
  } catch (error) {
    expect(error).toBeInstanceOf(ConfigError);
    expect((error as Error).name).toBe("ConfigError");
    return (error as Error).message;
  }
  throw new Error("parseConfig did not throw");
}

describe("AC-010 · Valid configuration with defaults", () => {
  it("test_ac_010_applies_defaults_and_normalises_tiles", () => {
    const raw = {
      tiles: [
        { id: " netflix ", title: "Netflix" },
        { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" },
        { type: "web", title: "El País", url: " https://elpais.com " },
      ],
    };
    const cfg = parseConfig(raw);
    expect(cfg.rowStyle).toBe("names");
    expect(cfg.clock).toBe("24");
    expect(cfg.background).toBe("night");
    expect(cfg.veil).toBe(80);
    expect(cfg.weather).toBeNull();
    expect(cfg.units).toBe("c");
    expect(cfg.tiles).toEqual([
      { type: "app", id: "netflix", title: "Netflix" },
      { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" },
      { type: "web", id: "web:https://elpais.com", title: "El País", url: "https://elpais.com" },
    ]);
  });

  it("test_ac_010_omits_absent_optional_keys", () => {
    const cfg = parseConfig({ tiles: [{ id: "a", title: "A" }] });
    expect(Object.keys(cfg.tiles[0]).sort()).toEqual(["id", "title", "type"]);
  });

  it("test_ac_010_full_config_keeps_values", () => {
    const cfg = parseConfig({
      tiles: [],
      rowStyle: "tiles",
      clock: "12",
      background: "monk",
      veil: 0,
      units: "f",
      weather: { name: "Madrid", lat: 40.4, lon: -3.7 },
    });
    expect(cfg.rowStyle).toBe("tiles");
    expect(cfg.clock).toBe("12");
    expect(cfg.background).toBe("monk");
    expect(cfg.veil).toBe(0);
    expect(cfg.units).toBe("f");
    expect(cfg.weather).toEqual({ name: "Madrid", lat: 40.4, lon: -3.7 });
  });

  it("test_ac_010_bundled_default_config_is_valid", () => {
    expect(() => parseConfig(defaultConfig)).not.toThrow();
  });

  it("test_ac_010_does_not_mutate_raw_nor_return_its_tiles", () => {
    const raw = { tiles: [{ id: " netflix ", title: "Netflix" }] };
    const cfg = parseConfig(raw);
    expect(raw.tiles[0].id).toBe(" netflix ");
    expect(cfg.tiles).not.toBe(raw.tiles);
  });
});

describe("AC-011 · Invalid configuration is rejected precisely", () => {
  const cases: Array<[string, unknown, string]> = [
    ["array", [], "debe ser un objeto"],
    ["empty object", {}, "tiles debe ser un array"],
    ["tile without id", { tiles: [{ title: "A" }] }, "posición 0"],
    ["javascript url", { tiles: [{ type: "web", title: "X", url: "javascript:alert(1)" }] }, "posición 0"],
    ["unknown type", { tiles: [{ type: "tv", id: "a", title: "A" }] }, "posición 0"],
    ["duplicate id", { tiles: [{ id: "a", title: "A" }, { id: "a", title: "B" }] }, "duplicado: a"],
    ["veil too high", { tiles: [], veil: 101 }, "veil"],
    ["veil fractional", { tiles: [], veil: 1.5 }, "veil"],
    ["bad rowStyle", { tiles: [], rowStyle: "grid" }, "rowStyle"],
    ["bad background", { tiles: [], background: "godrevy" }, "background"],
    ["bad latitude", { tiles: [], weather: { name: "X", lat: 91, lon: 0 } }, "weather"],
    ["icon on input", { tiles: [{ type: "input", id: "a", title: "A", icon: "x.png" }] }, "posición 0"],
  ];
  for (const [label, raw, expected] of cases) {
    it(`test_ac_011_rejects_${label.replace(/ /g, "_")}`, () => {
      expect(messageOf(raw)).toContain(expected);
    });
  }

  it("test_ac_011_rejects_bad_clock_and_units", () => {
    expect(messageOf({ tiles: [], clock: "13" })).toContain("clock");
    expect(messageOf({ tiles: [], units: "k" })).toContain("units");
  });

  it("test_ac_011_does_not_accept_javascript_url_in_any_case", () => {
    expect(() => parseConfig({ tiles: [{ type: "web", title: "X", url: "JavaScript:alert(1)" }] })).toThrow(ConfigError);
  });
});
