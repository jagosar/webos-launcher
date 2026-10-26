import { describe, it, expect } from "vitest";
import { ConfigError, DEFAULT_CONFIG, parseConfig } from "./config";

describe("parseConfig", () => {
  it("applies defaults and normalises tiles without mutating the input", () => {
    const raw = {
      tiles: [
        { id: " netflix ", title: " Netflix " },
        { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" },
        { type: "web", id: "ignored", title: "El País", url: " https://elpais.com ", extra: 1 },
      ],
    };
    const result = parseConfig(raw);
    expect(result).toEqual({
      ...DEFAULT_CONFIG,
      tiles: [
        { type: "app", id: "netflix", title: "Netflix" },
        { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" },
        { type: "web", id: "web:https://elpais.com", title: "El País", url: "https://elpais.com" },
      ],
    });
    expect("icon" in result.tiles[0]).toBe(false);
    expect("url" in result.tiles[0]).toBe(false);
    expect(raw.tiles[0].id).toBe(" netflix ");
    expect(result.tiles).not.toBe(raw.tiles);
  });

  it("keeps explicit values and the icon of app tiles", () => {
    const result = parseConfig({
      tiles: [{ id: "a", title: "A", icon: "a.png" }],
      rowStyle: "tiles",
      clock: "12",
      background: "monk",
      veil: 0,
      units: "f",
      weather: { name: " Madrid ", lat: 40.4, lon: -3.7 },
    });
    expect(result.rowStyle).toBe("tiles");
    expect(result.clock).toBe("12");
    expect(result.background).toBe("monk");
    expect(result.veil).toBe(0);
    expect(result.units).toBe("f");
    expect(result.weather).toEqual({ name: "Madrid", lat: 40.4, lon: -3.7 });
    expect(result.tiles[0].icon).toBe("a.png");
  });

  it("accepts weather null and http urls in any case", () => {
    const result = parseConfig({ tiles: [{ type: "web", title: "W", url: "HTTP://w.test" }], weather: null });
    expect(result.weather).toBeNull();
    expect(result.tiles[0].url).toBe("HTTP://w.test");
  });

  it.each([
    [[], "debe ser un objeto"],
    [{}, "tiles debe ser un array"],
    [{ tiles: [{ title: "A" }] }, "posición 0"],
    [{ tiles: [{ type: "web", title: "X", url: "javascript:alert(1)" }] }, "posición 0"],
    [{ tiles: [{ type: "web", title: "X" }] }, "posición 0"],
    [{ tiles: [{ type: "tv", id: "a", title: "A" }] }, "posición 0"],
    [{ tiles: [{ id: "a", title: "A" }, 5] }, "posición 1"],
    [{ tiles: [{ id: "a", title: "A", icon: "" }] }, "posición 0"],
    [{ tiles: [{ type: "input", id: "a", title: "A", icon: "x.png" }] }, "posición 0"],
    [{ tiles: [{ id: "a", title: "A" }, { id: "a", title: "B" }] }, "duplicado: a"],
    [{ tiles: [{ type: "web", title: "A", url: "http://x" }, { type: "web", title: "B", url: "http://x" }] }, "duplicado: web:http://x"],
    [{ tiles: [], veil: 101 }, "veil"],
    [{ tiles: [], veil: -1 }, "veil"],
    [{ tiles: [], veil: 1.5 }, "veil"],
    [{ tiles: [], rowStyle: "grid" }, "rowStyle"],
    [{ tiles: [], clock: "6" }, "clock"],
    [{ tiles: [], units: "k" }, "units"],
    [{ tiles: [], background: "godrevy" }, "background"],
    [{ tiles: [], weather: { name: "X", lat: 91, lon: 0 } }, "weather"],
    [{ tiles: [], weather: { name: "X", lat: 0, lon: 181 } }, "weather"],
    [{ tiles: [], weather: { name: "  ", lat: 0, lon: 0 } }, "weather"],
    [{ tiles: [], weather: { name: "X", lat: NaN, lon: 0 } }, "weather"],
    [{ tiles: [], weather: "Madrid" }, "weather"],
  ])("rejects %j", (raw, message) => {
    expect(() => parseConfig(raw)).toThrow(message);
  });

  it("throws ConfigError instances", () => {
    try {
      parseConfig(null);
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ConfigError);
      expect((error as Error).name).toBe("ConfigError");
    }
  });
});
