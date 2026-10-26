import { describe, it, expect, vi, afterEach } from "vitest";
import { getPlatform, loadLastIndex, saveLastIndex, LAST_INDEX_KEY } from "../src/system";
import type { KeyValueStore } from "../src/types";

type Globals = { webOSSystem?: unknown; PalmSystem?: unknown };

function memoryStore(initial?: string): KeyValueStore & { data: { [k: string]: string } } {
  const data: { [k: string]: string } = {};
  if (initial !== undefined) {
    data[LAST_INDEX_KEY] = initial;
  }
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v;
    },
  };
}

const throwingStore: KeyValueStore = {
  getItem: () => {
    throw new Error("denied");
  },
  setItem: () => {
    throw new Error("full");
  },
};

afterEach(() => {
  delete (window as unknown as Globals).webOSSystem;
  delete (window as unknown as Globals).PalmSystem;
});

describe("AC-014 · Platform and remembered tile", () => {
  it("test_ac_014_key_constant", () => {
    expect(LAST_INDEX_KEY).toBe("launcher.lastIndex");
  });

  it("test_ac_014_save_then_load_roundtrip_and_range", () => {
    const store = memoryStore();
    saveLastIndex(store, 3);
    expect(store.data[LAST_INDEX_KEY]).toBe("3");
    expect(loadLastIndex(store, 5)).toBe(3);
    expect(loadLastIndex(store, 2)).toBe(0);
  });

  it("test_ac_014_invalid_stored_values_give_zero", () => {
    expect(loadLastIndex(memoryStore("abc"), 5)).toBe(0);
    expect(loadLastIndex(memoryStore("-1"), 5)).toBe(0);
    expect(loadLastIndex(memoryStore("1.5"), 5)).toBe(0);
  });

  it("test_ac_014_null_store_and_zero_count", () => {
    expect(loadLastIndex(null, 5)).toBe(0);
    expect(() => saveLastIndex(null, 2)).not.toThrow();
    expect(loadLastIndex(memoryStore("0"), 0)).toBe(0);
  });

  it("test_ac_014_throwing_store_never_throws", () => {
    expect(loadLastIndex(throwingStore, 5)).toBe(0);
    expect(() => saveLastIndex(throwingStore, 2)).not.toThrow();
  });

  it("test_ac_014_getPlatform_returns_object_then_null", () => {
    const spy = vi.fn();
    (window as unknown as Globals).webOSSystem = { platformBack: spy };
    const platform = getPlatform();
    expect(platform).not.toBeNull();
    platform!.platformBack();
    expect(spy).toHaveBeenCalledTimes(1);

    delete (window as unknown as Globals).webOSSystem;
    expect(getPlatform()).toBeNull();
  });

  it("test_ac_014_getPlatform_falls_back_to_PalmSystem_and_requires_function", () => {
    (window as unknown as Globals).webOSSystem = {};
    expect(getPlatform()).toBeNull();
    const spy = vi.fn();
    (window as unknown as Globals).PalmSystem = { platformBack: spy };
    getPlatform()!.platformBack();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
