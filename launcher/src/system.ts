import type { KeyValueStore, PlatformApi } from "./types";

declare global {
  interface Window {
    webOSSystem?: PlatformApi;
    PalmSystem?: PlatformApi;
  }
}

export const LAST_INDEX_KEY = "launcher.lastIndex";

function hasPlatformBack(candidate: PlatformApi | undefined): candidate is PlatformApi {
  return !!candidate && typeof candidate.platformBack === "function";
}

export function getPlatform(): PlatformApi | null {
  if (typeof window === "undefined") {
    return null;
  }
  if (hasPlatformBack(window.webOSSystem)) {
    return window.webOSSystem;
  }
  return hasPlatformBack(window.PalmSystem) ? window.PalmSystem : null;
}

export function getStorage(): KeyValueStore | null {
  try {
    return (typeof window !== "undefined" && window.localStorage) || null;
  } catch (error) {
    return null;
  }
}

export function loadLastIndex(store: KeyValueStore | null, count: number): number {
  if (store === null || count <= 0) {
    return 0;
  }
  let stored: string | null;
  try {
    stored = store.getItem(LAST_INDEX_KEY);
  } catch (error) {
    return 0;
  }
  if (stored === null || !/^\d+$/.test(stored)) {
    return 0;
  }
  const index = Number(stored);
  return index < count ? index : 0;
}

export function saveLastIndex(store: KeyValueStore | null, index: number): void {
  if (store === null) {
    return;
  }
  try {
    store.setItem(LAST_INDEX_KEY, String(index));
  } catch (error) {
    // Storage may be full or denied; remembering the last tile is optional.
  }
}
