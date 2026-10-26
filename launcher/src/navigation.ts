import type { FocusState, NavKey } from "./types";

export const KEY_CODES: { readonly [code: number]: NavKey } = {
  37: "left",
  38: "up",
  39: "right",
  40: "down",
  13: "ok",
  461: "back",
  27: "back",
};

export function keyFromCode(keyCode: number): NavKey | null {
  if (!Object.prototype.hasOwnProperty.call(KEY_CODES, keyCode)) {
    return null;
  }
  return KEY_CODES[keyCode];
}

export function moveFocus(state: FocusState, key: NavKey): FocusState {
  if (state.count === 0) {
    return { index: 0, count: 0 };
  }
  if (key === "left") {
    return { index: Math.max(state.index - 1, 0), count: state.count };
  }
  if (key === "right") {
    return { index: Math.min(state.index + 1, state.count - 1), count: state.count };
  }
  return { index: state.index, count: state.count };
}
