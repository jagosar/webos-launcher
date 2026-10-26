import { describe, it, expect } from "vitest";
import { keyFromCode, moveFocus } from "../src/navigation";
import type { FocusState } from "../src/types";

describe("AC-004 · Remote keys and focus movement", () => {
  it("test_ac_004_maps_key_codes", () => {
    const codes = [37, 38, 39, 40, 13, 461, 27, 65];
    expect(codes.map((c) => keyFromCode(c))).toEqual([
      "left", "up", "right", "down", "ok", "back", "back", null,
    ]);
  });

  it("test_ac_004_unknown_and_prototype_names_are_null", () => {
    expect(keyFromCode(0)).toBeNull();
    expect(keyFromCode(NaN)).toBeNull();
  });

  it("test_ac_004_left_from_zero_stays_zero", () => {
    expect(moveFocus({ index: 0, count: 3 }, "left")).toEqual({ index: 0, count: 3 });
  });

  it("test_ac_004_right_three_times_clamps_without_wrap", () => {
    let state: FocusState = { index: 0, count: 3 };
    const seen: number[] = [];
    for (let i = 0; i < 3; i++) {
      state = moveFocus(state, "right");
      seen.push(state.index);
    }
    expect(seen).toEqual([1, 2, 2]);
    expect(state.index).not.toBe(0);
  });

  it("test_ac_004_left_moves_back_one", () => {
    expect(moveFocus({ index: 2, count: 3 }, "left").index).toBe(1);
  });

  it("test_ac_004_up_down_ok_back_keep_index", () => {
    for (const key of ["up", "down", "ok", "back"] as const) {
      expect(moveFocus({ index: 1, count: 3 }, key)).toEqual({ index: 1, count: 3 });
    }
  });

  it("test_ac_004_empty_row_stays_zero", () => {
    expect(moveFocus({ index: 0, count: 0 }, "right")).toEqual({ index: 0, count: 0 });
  });

  it("test_ac_004_does_not_mutate_input_and_returns_new_object", () => {
    const input: FocusState = { index: 1, count: 3 };
    const result = moveFocus(input, "right");
    expect(input).toEqual({ index: 1, count: 3 });
    expect(result).not.toBe(input);
    const same = moveFocus(input, "up");
    expect(same).not.toBe(input);
  });
});
