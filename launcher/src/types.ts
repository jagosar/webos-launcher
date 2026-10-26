export type TileType = "app" | "input" | "web";

/** One entry shown in the row. */
export interface Tile {
  readonly type: TileType;
  readonly id: string; // app/input: webOS app id; web: "web:" + url
  readonly title: string; // visible name
  readonly icon?: string; // only type "app"; path or URL relative to index.html
  readonly url?: string; // only type "web"; http(s) URL
}

export type RowStyle = "names" | "tiles";
export type ClockMode = "24" | "12";
export type Units = "c" | "f";

export const BACKGROUND_IDS = [
  "night",
  "dawn",
  "southampton",
  "penobscot",
  "northcape",
  "monk",
  "entrance",
  "chinasea",
] as const;
export type BackgroundId = typeof BACKGROUND_IDS[number];

export interface WeatherPlace {
  readonly name: string;
  readonly lat: number;
  readonly lon: number;
}

export interface LauncherConfig {
  readonly tiles: readonly Tile[];
  readonly rowStyle: RowStyle; // default "names"
  readonly clock: ClockMode; // default "24"
  readonly background: BackgroundId; // default "night"
  readonly veil: number; // 0..100 integer, default 80
  readonly weather: WeatherPlace | null; // default null
  readonly units: Units; // default "c"
}

/** Focus inside a row of `count` tiles. With count 0, index is 0. */
export interface FocusState {
  readonly index: number;
  readonly count: number;
}

export type NavKey = "left" | "right" | "up" | "down" | "ok" | "back";

export interface ClockText {
  readonly time: string; // "HH:MM", 24 h, zero-padded
  readonly date: string; // "<weekday>, <day> de <month>", Spanish, lower case
}

export interface ClockDisplay extends ClockText {
  readonly meridiem: "" | "AM" | "PM";
}

export type WeatherIcon = "sun" | "moon" | "cloud" | "fog" | "rain" | "snow" | "storm";

export interface WeatherNow {
  readonly temperature: number; // rounded
  readonly icon: WeatherIcon;
}

export interface WeatherView {
  readonly place: string;
  readonly now: WeatherNow | null;
}

export interface ViewModel {
  readonly tiles: readonly Tile[];
  readonly focusIndex: number;
  readonly rowStyle: RowStyle;
  readonly clock: ClockDisplay;
  readonly weather: WeatherView | null;
  readonly background: BackgroundId;
  readonly veil: number;
  readonly error: string | null;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface PlatformApi {
  platformBack(): void;
}

/** Minimal shape of webOS's PalmServiceBridge. */
export interface ServiceBridge {
  onservicecallback: ((message: string) => void) | null;
  call(uri: string, payload: string): void;
}
