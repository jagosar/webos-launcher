import { describe, it, expect, afterEach } from "vitest";
import { launchApp, launchTile, LG_HOME_ID, BROWSER_ID } from "../src/launch";
import type { ServiceBridge, Tile } from "../src/types";

interface FakeBridge extends ServiceBridge {
  calls: Array<{ uri: string; payload: string }>;
}

function makeFake(): FakeBridge {
  const fake: FakeBridge = {
    onservicecallback: null,
    calls: [],
    call(uri: string, payload: string): void {
      fake.calls.push({ uri, payload });
      if (fake.onservicecallback) {
        fake.onservicecallback('{"returnValue":true}');
      }
    },
  };
  return fake;
}

afterEach(() => {
  delete (window as unknown as { PalmServiceBridge?: unknown }).PalmServiceBridge;
});

const URI = "luna://com.webos.applicationManager/launch";

describe("AC-013 · Launching tiles of every type", () => {
  it("test_ac_013_constants", () => {
    expect(LG_HOME_ID).toBe("com.webos.app.home");
    expect(BROWSER_ID).toBe("com.webos.app.browser");
  });

  it("test_ac_013_app_tile_sends_only_id", async () => {
    const fake = makeFake();
    const tile: Tile = { type: "app", id: "netflix", title: "Netflix" };
    await launchTile(tile, () => fake);
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].uri).toBe(URI);
    expect(JSON.parse(fake.calls[0].payload)).toEqual({ id: "netflix" });
  });

  it("test_ac_013_input_tile_sends_only_id", async () => {
    const fake = makeFake();
    const tile: Tile = { type: "input", id: "com.webos.app.hdmi1", title: "HDMI 1" };
    await launchTile(tile, () => fake);
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].uri).toBe(URI);
    expect(JSON.parse(fake.calls[0].payload)).toEqual({ id: "com.webos.app.hdmi1" });
    expect(JSON.parse(fake.calls[0].payload)).not.toHaveProperty("params");
  });

  it("test_ac_013_web_tile_opens_browser_with_target", async () => {
    const fake = makeFake();
    const tile: Tile = { type: "web", id: "web:https://elpais.com", title: "El País", url: "https://elpais.com" };
    await launchTile(tile, () => fake);
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].uri).toBe(URI);
    expect(JSON.parse(fake.calls[0].payload)).toEqual({
      id: "com.webos.app.browser",
      params: { target: "https://elpais.com" },
    });
  });

  it("test_ac_013_launchApp_without_params_has_no_params_key", async () => {
    const fake = makeFake();
    await launchApp("x", () => fake);
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].payload).toBe(JSON.stringify({ id: "x" }));
    expect(JSON.parse(fake.calls[0].payload)).not.toHaveProperty("params");
  });

  it("test_ac_013_web_tile_without_url_rejects", async () => {
    const fake = makeFake();
    const tile: Tile = { type: "web", id: "web:", title: "W" };
    await expect(launchTile(tile, () => fake)).rejects.toThrow("Falta la url");
    expect(fake.calls).toHaveLength(0);
  });
});
