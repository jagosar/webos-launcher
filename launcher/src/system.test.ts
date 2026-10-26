import { describe, it, expect, vi, afterEach } from "vitest";
import {
  LAST_INDEX_KEY,
  getPlatform,
  getStorage,
  loadLastIndex,
  saveLastIndex,
} from "./system";
import type { KeyValueStore } from "./types";

function createMemoryStore(initial?: string): KeyValueStore {
  const data: { [key: string]: string } = {};
  if (initial !== undefined) {
    data[LAST_INDEX_KEY] = initial;
  }
  return {
    getItem: (key: string): string | null => (key in data ? data[key] : null),
    setItem: (key: string, value: string): void => { data[key] = value; },
  };
}

const throwingStore: KeyValueStore = {
  getItem: (): string | null => { throw new Error("denied"); },
  setItem: (): void => { throw new Error("full"); },
};

afterEach(() => {
  delete window.webOSSystem;
  delete window.PalmSystem;
});

describe("loadLastIndex / saveLastIndex", () => {
  it("round-trips an index inside range", () => {
    const store = createMemoryStore();
    saveLastIndex(store, 3);
    expect(loadLastIndex(store, 5)).toBe(3);
  });

  it("returns 0 when the index is out of range", () => {
    const store = createMemoryStore();
    saveLastIndex(store, 3);
    expect(loadLastIndex(store, 2)).toBe(0);
  });

  it("returns 0 for non-integer or negative values", () => {
    expect(loadLastIndex(createMemoryStore("abc"), 5)).toBe(0);
    expect(loadLastIndex(createMemoryStore("-1"), 5)).toBe(0);
    expect(loadLastIndex(createMemoryStore("1.5"), 5)).toBe(0);
  });

  it("returns 0 with no value, null store or zero count", () => {
    expect(loadLastIndex(createMemoryStore(), 5)).toBe(0);
    expect(loadLastIndex(null, 5)).toBe(0);
    expect(loadLastIndex(createMemoryStore("1"), 0)).toBe(0);
  });

  it("does not throw when the store throws", () => {
    expect(loadLastIndex(throwingStore, 5)).toBe(0);
    expect(() => saveLastIndex(throwingStore, 1)).not.toThrow();
  });

  it("does nothing when saving to a null store", () => {
    expect(() => saveLastIndex(null, 1)).not.toThrow();
  });
});

describe("getPlatform", () => {
  it("returns webOSSystem when it has platformBack", () => {
    const platformBack = vi.fn();
    window.webOSSystem = { platformBack };
    getPlatform()?.platformBack();
    expect(platformBack).toHaveBeenCalledTimes(1);
  });

  it("falls back to PalmSystem", () => {
    const palm = { platformBack: vi.fn() };
    window.PalmSystem = palm;
    expect(getPlatform()).toBe(palm);
  });

  it("returns null when neither exists or platformBack is not a function", () => {
    expect(getPlatform()).toBeNull();
    window.webOSSystem = {} as unknown as { platformBack(): void };
    expect(getPlatform()).toBeNull();
  });
});

describe("getStorage", () => {
  it("returns window.localStorage", () => {
    expect(getStorage()).toBe(window.localStorage);
  });

  it("returns null when accessing localStorage throws", () => {
    const spy = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(getStorage()).toBeNull();
    spy.mockRestore();
  });
});
