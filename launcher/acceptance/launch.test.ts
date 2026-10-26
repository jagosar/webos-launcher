import { describe, it, expect, afterEach } from "vitest";
import { launchApp, LaunchError, LAUNCH_URI } from "../src/launch";
import type { ServiceBridge } from "../src/types";

interface FakeBridge extends ServiceBridge {
  calls: Array<{ uri: string; payload: string }>;
}

function makeFake(): FakeBridge {
  const fake: FakeBridge = {
    onservicecallback: null,
    calls: [],
    call(uri: string, payload: string): void {
      fake.calls.push({ uri, payload });
    },
  };
  return fake;
}

async function settle(p: Promise<void>): Promise<{ ok: boolean; error?: unknown }> {
  try {
    await p;
    return { ok: true };
  } catch (error) {
    return { ok: false, error };
  }
}

afterEach(() => {
  delete (window as unknown as { PalmServiceBridge?: unknown }).PalmServiceBridge;
});

describe("AC-005 · Launching through the Luna bus", () => {
  it("test_ac_005_uri_constant", () => {
    expect(LAUNCH_URI).toBe("luna://com.webos.applicationManager/launch");
  });

  it("test_ac_005_resolves_on_return_value_true_with_single_call", async () => {
    const fake = makeFake();
    const p = launchApp("netflix", () => fake);
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].uri).toBe("luna://com.webos.applicationManager/launch");
    expect(JSON.parse(fake.calls[0].payload)).toEqual({ id: "netflix" });
    fake.onservicecallback!('{"returnValue":true}');
    await expect(p).resolves.toBeUndefined();
    expect(fake.calls).toHaveLength(1);
  });

  it("test_ac_005_callback_is_set_before_call", async () => {
    let callbackWasSet = false;
    const fake = makeFake();
    fake.call = (uri: string, payload: string): void => {
      callbackWasSet = typeof fake.onservicecallback === "function";
      fake.calls.push({ uri, payload });
      fake.onservicecallback!('{"returnValue":true}');
    };
    await launchApp("netflix", () => fake);
    expect(callbackWasSet).toBe(true);
  });

  it("test_ac_005_rejects_with_error_text", async () => {
    const fake = makeFake();
    const p = launchApp("netflix", () => fake);
    fake.onservicecallback!('{"returnValue":false,"errorText":"App no instalada"}');
    const r = await settle(p);
    expect(r.ok).toBe(false);
    expect(r.error).toBeInstanceOf(LaunchError);
    expect((r.error as Error).name).toBe("LaunchError");
    expect((r.error as Error).message).toBe("App no instalada");
  });

  it("test_ac_005_rejects_unknown_error_without_error_text", async () => {
    const fake = makeFake();
    const p = launchApp("netflix", () => fake);
    fake.onservicecallback!('{"returnValue":false}');
    const r = await settle(p);
    expect(r.error).toBeInstanceOf(LaunchError);
    expect((r.error as Error).message).toBe("Error desconocido");
  });

  it("test_ac_005_rejects_invalid_json", async () => {
    const fake = makeFake();
    const p = launchApp("netflix", () => fake);
    fake.onservicecallback!("not json");
    const r = await settle(p);
    expect(r.error).toBeInstanceOf(LaunchError);
    expect((r.error as Error).message).toBe("Respuesta inválida");
  });

  it("test_ac_005_rejects_when_palm_service_bridge_is_missing", async () => {
    const r = await settle(launchApp("x"));
    expect(r.ok).toBe(false);
    expect(r.error).toBeInstanceOf(LaunchError);
    expect((r.error as Error).message).toBe("PalmServiceBridge no disponible");
  });

  it("test_ac_005_uses_window_palm_service_bridge_by_default", async () => {
    const created: FakeBridge[] = [];
    (window as unknown as { PalmServiceBridge: unknown }).PalmServiceBridge = function () {
      const f = makeFake();
      created.push(f);
      return f;
    };
    const p = launchApp("netflix");
    expect(created).toHaveLength(1);
    expect(created[0].calls).toHaveLength(1);
    created[0].onservicecallback!('{"returnValue":true}');
    await expect(p).resolves.toBeUndefined();
  });

  it("test_ac_005_stays_pending_until_callback_fires", async () => {
    const fake = makeFake();
    let settled = false;
    const p = launchApp("netflix", () => fake);
    p.then(
      () => { settled = true; },
      () => { settled = true; }
    );
    await Promise.resolve();
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(fake.calls).toHaveLength(1);
    fake.onservicecallback!('{"returnValue":true}');
    await p;
  });

  it("test_ac_005_only_first_callback_counts", async () => {
    const fake = makeFake();
    const p = launchApp("netflix", () => fake);
    fake.onservicecallback!('{"returnValue":true}');
    fake.onservicecallback!('{"returnValue":false,"errorText":"late"}');
    await expect(p).resolves.toBeUndefined();
    expect(fake.calls).toHaveLength(1);
  });

  it("test_ac_005_factory_throw_becomes_rejection", async () => {
    const r = await settle(
      launchApp("x", () => {
        throw new LaunchError("boom");
      })
    );
    expect(r.error).toBeInstanceOf(LaunchError);
    expect((r.error as Error).message).toBe("boom");
  });
});
