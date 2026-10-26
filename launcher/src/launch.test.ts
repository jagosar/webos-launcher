import { describe, it, expect, vi, afterEach, type Mock } from "vitest";
import {
  BROWSER_ID,
  LAUNCH_URI,
  LG_HOME_ID,
  LaunchError,
  defaultBridgeFactory,
  launchApp,
  launchTile,
} from "./launch";
import type { ServiceBridge, Tile } from "./types";

interface FakeBridge extends ServiceBridge {
  call: Mock<(uri: string, payload: string) => void>;
}

function createFakeBridge(): FakeBridge {
  return { onservicecallback: null, call: vi.fn<(uri: string, payload: string) => void>() };
}

afterEach(() => {
  delete window.PalmServiceBridge;
});

describe("launchApp", () => {
  it("calls the bridge once with the launch URI and payload, and resolves on success", async () => {
    const bridge = createFakeBridge();
    const promise = launchApp("netflix", () => bridge);
    expect(bridge.call).toHaveBeenCalledTimes(1);
    expect(bridge.call.mock.calls[0][0]).toBe(LAUNCH_URI);
    expect(JSON.parse(bridge.call.mock.calls[0][1])).toEqual({ id: "netflix" });
    bridge.onservicecallback?.('{"returnValue":true}');
    await expect(promise).resolves.toBeUndefined();
  });

  it("stays pending until the callback fires", async () => {
    const bridge = createFakeBridge();
    let isSettled = false;
    launchApp("a", () => bridge).then(() => { isSettled = true; });
    await Promise.resolve();
    expect(isSettled).toBe(false);
  });

  it("sets the callback before calling, even if the bridge answers synchronously", async () => {
    const bridge = createFakeBridge();
    bridge.call.mockImplementation(() => bridge.onservicecallback?.('{"returnValue":true}'));
    await expect(launchApp("a", () => bridge)).resolves.toBeUndefined();
  });

  it("rejects with the errorText on failure", async () => {
    const bridge = createFakeBridge();
    const promise = launchApp("a", () => bridge);
    bridge.onservicecallback?.('{"returnValue":false,"errorText":"App no instalada"}');
    await expect(promise).rejects.toMatchObject({ name: "LaunchError", message: "App no instalada" });
  });

  it("rejects with a default message when errorText is missing", async () => {
    const bridge = createFakeBridge();
    const promise = launchApp("a", () => bridge);
    bridge.onservicecallback?.('{"returnValue":false}');
    await expect(promise).rejects.toThrow("Error desconocido");
  });

  it("rejects on unparsable JSON", async () => {
    const bridge = createFakeBridge();
    const promise = launchApp("a", () => bridge);
    bridge.onservicecallback?.("not json");
    const error = await promise.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(LaunchError);
    expect((error as LaunchError).message).toBe("Respuesta inválida");
  });

  it("ignores callbacks after the first", async () => {
    const bridge = createFakeBridge();
    const promise = launchApp("a", () => bridge);
    bridge.onservicecallback?.('{"returnValue":true}');
    bridge.onservicecallback?.('{"returnValue":false,"errorText":"late"}');
    await expect(promise).resolves.toBeUndefined();
  });

  it("rejects when the bridge is unavailable", async () => {
    delete window.PalmServiceBridge;
    await expect(launchApp("x")).rejects.toMatchObject({
      name: "LaunchError",
      message: "PalmServiceBridge no disponible",
    });
  });

  it("rejects with the factory error", async () => {
    const failure = new LaunchError("boom");
    await expect(launchApp("x", () => { throw failure; })).rejects.toBe(failure);
  });
});

describe("launchApp params", () => {
  it("sends params in the payload when given", () => {
    const bridge = createFakeBridge();
    void launchApp("x", () => bridge, { target: "https://a.test" });
    expect(JSON.parse(bridge.call.mock.calls[0][1])).toEqual({ id: "x", params: { target: "https://a.test" } });
  });

  it("sends no params key when omitted", () => {
    const bridge = createFakeBridge();
    void launchApp("x", () => bridge);
    expect(bridge.call.mock.calls[0][1]).toBe('{"id":"x"}');
  });
});

describe("launchTile", () => {
  function payloadOf(tile: Tile): unknown {
    const bridge = createFakeBridge();
    void launchTile(tile, () => bridge);
    expect(bridge.call).toHaveBeenCalledTimes(1);
    return JSON.parse(bridge.call.mock.calls[0][1]);
  }

  it("launches app and input tiles by id without params", () => {
    expect(payloadOf({ type: "app", id: "netflix", title: "N" })).toEqual({ id: "netflix" });
    expect(payloadOf({ type: "input", id: "com.webos.app.hdmi1", title: "H" })).toEqual({ id: "com.webos.app.hdmi1" });
  });

  it("opens web tiles in the browser with the target", () => {
    expect(payloadOf({ type: "web", id: "web:https://e.test", title: "E", url: "https://e.test" })).toEqual({
      id: BROWSER_ID,
      params: { target: "https://e.test" },
    });
  });

  it("rejects a web tile without url", async () => {
    await expect(launchTile({ type: "web", id: "web:", title: "E" }, () => createFakeBridge())).rejects.toMatchObject({
      name: "LaunchError",
      message: "Falta la url",
    });
  });

  it("exposes the LG Home id", () => {
    expect(LG_HOME_ID).toBe("com.webos.app.home");
  });
});

describe("defaultBridgeFactory", () => {
  it("creates a bridge from window.PalmServiceBridge", () => {
    const instance = createFakeBridge();
    window.PalmServiceBridge = function () { return instance; } as unknown as new () => ServiceBridge;
    expect(defaultBridgeFactory()).toBe(instance);
  });

  it("throws LaunchError when missing", () => {
    expect(() => defaultBridgeFactory()).toThrow(LaunchError);
  });
});
