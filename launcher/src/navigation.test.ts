import { describe, it, expect } from "vitest";
import { keyFromCode, moveFocus } from "./navigation";

describe("keyFromCode", () => {
  it("maps remote and keyboard codes", () => {
    expect([37, 38, 39, 40, 13, 461, 27].map(keyFromCode)).toEqual([
      "left", "up", "right", "down", "ok", "back", "back",
    ]);
  });

  it("returns null for unknown codes and prototype names", () => {
    expect(keyFromCode(65)).toBeNull();
    expect(keyFromCode("toString" as unknown as number)).toBeNull();
  });
});

describe("moveFocus", () => {
  it("does not go below zero", () => {
    expect(moveFocus({ index: 0, count: 3 }, "left")).toEqual({ index: 0, count: 3 });
  });

  it("moves right without wrapping", () => {
    let state = { index: 0, count: 3 };
    const indexes: number[] = [];
    for (let i = 0; i < 3; i++) {
      state = moveFocus(state, "right");
      indexes.push(state.index);
    }
    expect(indexes).toEqual([1, 2, 2]);
  });

  it("moves left from the middle", () => {
    expect(moveFocus({ index: 2, count: 3 }, "left").index).toBe(1);
  });

  it("keeps the index on other keys", () => {
    for (const key of ["up", "down", "ok", "back"] as const) {
      expect(moveFocus({ index: 1, count: 3 }, key)).toEqual({ index: 1, count: 3 });
    }
  });

  it("returns an empty state for an empty row", () => {
    expect(moveFocus({ index: 0, count: 0 }, "right")).toEqual({ index: 0, count: 0 });
  });

  it("returns a new object and does not mutate the input", () => {
    const state = { index: 1, count: 3 };
    const result = moveFocus(state, "up");
    expect(result).not.toBe(state);
    expect(state).toEqual({ index: 1, count: 3 });
  });
});
