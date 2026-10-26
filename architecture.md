# Architecture: Lightkeeper-style launcher (v2)
Type: change · Size: large

## 1. Goal
Evolve the minimal webOS 4 launcher (design 0001, code in `launcher/`) with the ideas of [chiotas/lightkeeper](https://github.com/chiotas/lightkeeper) that work **without root**: a calm Home over a picture with a dark veil, the time (24 or 12 h) and the current weather for one city, and a row of apps, HDMI inputs and web pages that **slides** so the focused item is always on screen (this fixes QA defect d1 of the first run). The default row style becomes Lightkeeper's "plain names" (text, the selected one brighter with a short line under it); the square tiles stay as an option. Everything is configured in one bundled `config.json`. Back leaves the launcher, Down opens LG's Home, and the last opened tile is remembered.

## 2. Scope
### Included
- `launcher/src/config.json` replaces `launcher/src/apps.json`: tiles (`app`, `input`, `web`), row style, clock mode, background, veil strength, weather city and units. Invalid config → defaults + visible error.
- Row that slides horizontally (CSS `transform: translateX`) to keep the focused tile inside the 1696 px row viewport.
- Two row styles: `names` (default) and `tiles` (v1 look).
- Background picture (bundled JPGs) with a veil whose opacity is `veil / 100`.
- Clock 24 h (as v1) or 12 h (`7:05` + `PM`), date in Spanish (as v1).
- Weather from Open-Meteo (no API key) refreshed every 30 min; on failure only the city name is shown.
- Keys: Left/Right move, OK opens, **Back → `webOSSystem.platformBack()`**, **Down → LG Home (`com.webos.app.home`)**, Up does nothing.
- Input tiles (`com.webos.app.hdmi1`…) launch like apps; web tiles open the TV browser with `params.target`.
- Remember the last opened tile index in `localStorage` and focus it on start.
- Bundled assets: Jost font (SIL OFL 1.1) and 8 backgrounds (public domain / CC0 paintings and Lightkeeper's own MIT gradients), copied by the architect before implementation (section 4, `launcher/public/assets/`).
### Excluded (non-goals)
- Anything needing root, Homebrew Channel or GlassHouse: taking the Home key, opening at power on, the phone settings server on port 8090, the open-apps list, closing apps, reading installed apps or their icons from the TV.
- An on-TV settings panel; random backgrounds; clock styles other than Jost; brand logos (only the `icon` field from v1, unchanged).
- The Godrevy Lighthouse photograph (all rights reserved; never copy it).
- Any change outside `launcher/`.

## 3. Stack
Unchanged from design 0001 (see `launcher/package.json`): TypeScript 5.9.3 (`strict`), esbuild 0.28.2 (IIFE, `target: ["chrome53"]`), Vitest 5.0.5 + jsdom 30.1.3, acorn 8.19.0, @types/node 22.20.5, Node ≥ 22.12, npm. **No new dependencies.** Network at runtime: `https://api.open-meteo.com` (HTTPS GET from the TV).

Commands (from the repo root, unchanged):
- install `npm --prefix launcher install` · build `npm --prefix launcher run build` · types `npm --prefix launcher run typecheck` · unit `npm --prefix launcher test` · acceptance `npm --prefix launcher run acceptance` · QA `npm --prefix launcher run qa` · lint: not applicable.

## 4. Repository structure
Only `launcher/` changes. `(=)` unchanged, `(new)`, `(chg)` changed, `(del)` deleted. Owner in brackets; `—` means nobody may change it.

```
launcher/
  package.json, package-lock.json, tsconfig.json, tsconfig.test.json, vitest.config.ts, .gitignore   (=) —
  scripts/build.mjs            (chg) [WP-01]
  public/index.html            (=)   —
  public/appinfo.json          (chg) [WP-01]
  public/style.css             (chg) [WP-05]
  public/assets/               (new) —   copied by the architect before /implement (see below)
  src/types.ts                 (chg) [WP-01]
  src/config.json              (new) [WP-01]
  src/weather-icons.ts         (new) [WP-01]
  src/apps.json                (del) [WP-01]
  src/config.ts                (new) [WP-02]
  src/config.test.ts           (new) [WP-02]
  src/apps.ts                  (del) [WP-02]
  src/apps.test.ts             (del) [WP-02]
  src/clock.ts                 (chg) [WP-02]
  src/clock.test.ts            (chg) [WP-02]
  src/launch.ts                (chg) [WP-03]
  src/launch.test.ts           (chg) [WP-03]
  src/system.ts                (new) [WP-03]
  src/system.test.ts           (new) [WP-03]
  src/weather.ts               (new) [WP-04]
  src/weather.test.ts          (new) [WP-04]
  src/view.ts                  (chg) [WP-05]
  src/view.test.ts             (chg) [WP-05]
  src/navigation.ts, src/navigation.test.ts   (=) —
  src/main.ts                  (chg) [WP-06]
  src/main.test.ts             (chg) [WP-06]
  src/entry.ts                 (chg) [WP-06]
  acceptance/                  [AT]
  qa/                          [QA]
  README.md                    [DOCS]
```

`launcher/public/assets/` (binary, already present when `/implement` starts; nobody edits it):
```
assets/CREDITS.md                       sources and licences of every asset
assets/fonts/jost-200.woff2, jost-300.woff2, jost-500.woff2, jost.woff2, OFL.txt
assets/backgrounds/night.jpg, dawn.jpg, southampton.jpg, penobscot.jpg,
                   northcape.jpg, monk.jpg, entrance.jpg, chinasea.jpg   (1920×1080)
assets/backgrounds/LICENSE-lightkeeper.txt   (MIT notice for night/dawn)
```

## 4b. Existing code context
| What an implementer needs to know | Where it is (`file:lines`) | Pattern or rule |
|---|---|---|
| Domain error class + exact messages | `launcher/src/launch.ts:9-11` (v1 `apps.ts:3-5`, `:11-17`, deleted by WP-02) | `class XError extends Error { name = "XError"; }`; messages in Spanish with the 0-based `posición <n>`. `ConfigError` follows it. |
| Validation of untrusted JSON | v1 `apps.ts:7-47` (deleted by WP-02; described here) | `isPlainObject` guard, `requireText` trims, returns new objects, `indexOf` for duplicates (no `includes`). |
| Promise + callback style, no async/await | `launcher/src/launch.ts:24-62` | `new Promise`, `isSettled` guard, set `onservicecallback` before `call`. Mirror for `weather.ts`'s `httpGet`. |
| Optional globals declared on `Window` | `launcher/src/launch.ts:3-7` | `declare global { interface Window { … } }`; mirror for `webOSSystem`/`PalmSystem` in `system.ts`. |
| DOM building helper | `launcher/src/view.ts:5-28` | `createWithRole(doc, tag, role, text?)`, `data-role` attributes, `textContent` only. Keep the existing `data-role` names. |
| Wiring, state and injected dependencies | `launcher/src/main.ts:23-96` | Options object with optional injected deps and defaults; mutable state only here; `isLaunching` / `isStopped` guards; `stop()` clears timers and the listener. |
| Clock formatting, zero padding without `padStart` | `launcher/src/clock.ts:1-29` | `pad2`, Spanish names arrays. `formatClock` must stay byte-for-byte compatible. |
| Key mapping (unchanged) | `launcher/src/navigation.ts:3-31` | `keyFromCode`, `moveFocus` (no wrap). Reuse; do not edit. |
| Build script | `launcher/scripts/build.mjs:1-88` | Paths from `import.meta.url`; `assertInputsExist`; IIFE chrome53 bundle; solid PNG icons. |
| Unit test style | `launcher/src/launch.test.ts:1-40`, `launcher/src/main.test.ts:1-40` | `import { describe, it, expect, vi } from "vitest"`, doubles inside the file, `vi.useFakeTimers()` before starting timers. |
| Keyboard events in jsdom | `launcher/src/main.test.ts:27-31` | `new KeyboardEvent("keydown",{keyCode,cancelable:true,bubbles:true})` + `Object.defineProperty(ev,"keyCode",{value})`. |
| CSS constraints for Chromium 53 | `launcher/public/style.css:1-95` | No flex `gap`, no `inset`, transitions on `transform`/`opacity` only. |
| Data flow (new) | — | `config.json` → `parseConfig` → `main` state → `view.update(model)`; keydown → `keyFromCode` → `moveFocus` / `launch` / `platformBack`; timers → clock re-render, weather refresh. |
| What must NOT change | `navigation.ts`, `public/index.html`, tooling files, `formatClock`, `launchApp(appId, bridgeFactory)` behaviour | AC-003, AC-004 and AC-005 of design 0001 stay valid and their acceptance files stay as they are. |

**Lightkeeper references** (MIT, © 2026 Francesco Gola; not in this repo, so the relevant pieces are copied here; credit it in a comment where used):
- Veil CSS (`app/index.html`): `background: radial-gradient(1100px 620px at 0 0, rgba(18,13,8,.92) 0, rgba(18,13,8,0) 100%), linear-gradient(to top, rgba(18,13,8,1) 0, rgba(18,13,8,.7) 20%, rgba(18,13,8,0) 50%);`
- Layout: body `1920×1080`, text `#f4f1ec`, `text-shadow: 0 2px 24px rgba(0,0,0,.5)`; header at `top:68px; left:112px`; clock `150px` weight 200, `letter-spacing:-.02em`, AM/PM `52px` weight 300 `margin-left:14px`; weather `margin-top:18px; font-size:34px; weight 300; opacity:.9`, icon 34×34 `margin-left:14px`; row at `left:112px; bottom:104px`, `transition: transform .2s`.
- Names style: item `position:relative; margin-right:72px; font-size:44px; font-weight:400; opacity:.72; transition: opacity .2s`; selected `opacity:1; font-weight:500`; selected `::after { content:""; position:absolute; left:0; bottom:-20px; width:36px; height:3px; border-radius:2px; background:currentColor }`.
- Row slide (`seleziona`): viewport `1920 - 224`; if `left + width > offset + viewport` then `offset = left + width - viewport`; if `left < offset` then `offset = left`; apply `translateX(-offset px)`.
- Weather (`meteo`, `icona`): URL and WMO-code mapping as in section 6 `weather.ts`.

## 5. Data model
`launcher/src/types.ts` (WP-01). Keep `NavKey`, `ClockText`, `ServiceBridge`, `FocusState` exactly as they are; remove `AppEntry` (replaced by `Tile`). Add:

```ts
export type TileType = "app" | "input" | "web";
export interface Tile {
  readonly type: TileType;
  readonly id: string;        // app/input: webOS app id; web: "web:" + url
  readonly title: string;
  readonly icon?: string;     // only type "app"; path or URL (v1 semantics)
  readonly url?: string;      // only type "web"; http(s) URL
}
export type RowStyle = "names" | "tiles";
export type ClockMode = "24" | "12";
export type Units = "c" | "f";
export const BACKGROUND_IDS = ["night", "dawn", "southampton", "penobscot", "northcape", "monk", "entrance", "chinasea"] as const;
export type BackgroundId = typeof BACKGROUND_IDS[number];
export interface WeatherPlace { readonly name: string; readonly lat: number; readonly lon: number; }
export interface LauncherConfig {
  readonly tiles: readonly Tile[];
  readonly rowStyle: RowStyle;       // default "names"
  readonly clock: ClockMode;         // default "24"
  readonly background: BackgroundId; // default "night"
  readonly veil: number;             // 0..100 integer, default 80
  readonly weather: WeatherPlace | null; // default null
  readonly units: Units;             // default "c"
}
export interface ClockDisplay extends ClockText { readonly meridiem: "" | "AM" | "PM"; }
export type WeatherIcon = "sun" | "moon" | "cloud" | "fog" | "rain" | "snow" | "storm";
export interface WeatherNow { readonly temperature: number; readonly icon: WeatherIcon; } // temperature rounded
export interface WeatherView { readonly place: string; readonly now: WeatherNow | null; }
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
export interface KeyValueStore { getItem(key: string): string | null; setItem(key: string, value: string): void; }
export interface PlatformApi { platformBack(): void; }
```

`launcher/src/config.json` (WP-01), default content:
```json
{
  "rowStyle": "names",
  "clock": "24",
  "background": "night",
  "veil": 80,
  "weather": { "name": "Madrid", "lat": 40.4168, "lon": -3.7038 },
  "units": "c",
  "tiles": [
    { "type": "input", "id": "com.webos.app.livetv", "title": "TV" },
    { "id": "youtube.leanback.v4", "title": "YouTube" },
    { "id": "netflix", "title": "Netflix" },
    { "id": "amazon", "title": "Prime Video" },
    { "type": "input", "id": "com.webos.app.hdmi1", "title": "HDMI 1" },
    { "type": "web", "title": "El País", "url": "https://elpais.com" }
  ]
}
```

`launcher/public/appinfo.json` (WP-01): the v1 file plus `"bgColor": "#000000"` and `"disableBackHistoryAPI": true` (so Back reaches the page as keyCode 461 and `platformBack` decides). `version` → `"2.0.0"` (this design is release 2.0; `package.json` and its lockfile carry the same `2.0.0`).

## 6. Contracts between modules
Relative imports without extension, as in v1.

### `config.ts` (WP-02)
```ts
export class ConfigError extends Error { name = "ConfigError"; }
export const DEFAULT_CONFIG: LauncherConfig; // tiles [], names, "24", "night", 80, null, "c"
export function parseConfig(raw: unknown): LauncherConfig;
```
- Returns a new object; never mutates `raw`. Optional tile keys (`icon`, `url`) are **omitted** when absent, never set to `undefined`. Unknown keys (top level and tiles) are ignored. A `web` tile's own `id`, if any, is ignored and replaced by `"web:" + url`. `weather.name` is trimmed and must be non-empty. Missing optional fields take `DEFAULT_CONFIG` values; `tiles` is required.
- Tiles: `type` defaults to `"app"`. `app`/`input`: `id` and `title` required (trimmed, non-empty); `icon` allowed only for `app` (non-empty string). `web`: `title` and `url` required; `url` trimmed, must start with `http://` or `https://` (case-insensitive); the result has `id: "web:" + url`, `url`, no `icon`.
- Errors (`ConfigError`), messages must contain:
  - `raw` not a plain object → `La configuración debe ser un objeto`;
  - `tiles` missing or not an array → `tiles debe ser un array`;
  - a bad tile (not an object, unknown `type`, missing/empty field, bad `icon`, bad `url`) → `posición <index>`;
  - duplicate tile `id` → `duplicado: <id>`;
  - `rowStyle`, `clock`, `units`, `background` not one of their values → the field name (`rowStyle`, `clock`, `units`, `background`);
  - `veil` not an integer in 0..100 → `veil`;
  - `weather` not `null` and not `{ name: non-empty string, lat: number in [-90, 90], lon: number in [-180, 180] }` → `weather`.

### `clock.ts` (WP-02)
```ts
export function formatClock(now: Date): ClockText;                       // unchanged (AC-003)
export function formatClockWithMode(now: Date, mode: ClockMode): ClockDisplay;
```
- `"24"` → `{ time: formatClock(now).time, meridiem: "", date }`.
- `"12"` → hour `((h + 11) % 12) + 1` **without** padding, minutes padded: `7:05`, `12:30`; `meridiem` `"AM"` if `h < 12` else `"PM"`; same `date`.

### `launch.ts` (WP-03)
```ts
export function launchApp(appId: string, bridgeFactory?: () => ServiceBridge, params?: { [key: string]: string }): Promise<void>;
export const LG_HOME_ID = "com.webos.app.home";
export const BROWSER_ID = "com.webos.app.browser";
export function launchTile(tile: Tile, bridgeFactory?: () => ServiceBridge): Promise<void>;
```
- `launchApp` keeps v1 behaviour (AC-005). With `params` the payload is `JSON.stringify({ id: appId, params })`; without it, exactly `{ id: appId }` (no `params` key).
- `launchTile`: `app`/`input` → `launchApp(tile.id, bridgeFactory)`; `web` → `launchApp(BROWSER_ID, bridgeFactory, { target: tile.url })` after narrowing: a `web` tile without `url` rejects `LaunchError("Falta la url")` (cannot happen after `parseConfig`, but `url` is optional in the type).

### `system.ts` (WP-03)
```ts
export const LAST_INDEX_KEY = "launcher.lastIndex";
export function getPlatform(): PlatformApi | null;   // window.webOSSystem || window.PalmSystem, if it has a platformBack function; else null
export function getStorage(): KeyValueStore | null;  // window.localStorage; null if absent or if accessing it throws
export function loadLastIndex(store: KeyValueStore | null, count: number): number;
export function saveLastIndex(store: KeyValueStore | null, index: number): void;
```
- `loadLastIndex`: reads `LAST_INDEX_KEY`; returns the integer if it parses as an integer in `[0, count-1]`, else `0`; `0` when `store` is null, `count` is 0 or `getItem` throws.
- `saveLastIndex`: `setItem(LAST_INDEX_KEY, String(index))`; swallows exceptions from `setItem` (storage full/denied) — the only allowed swallow, with a comment; no-op with `null`.

### `weather.ts` (WP-04)
```ts
export class WeatherError extends Error { name = "WeatherError"; }
export const WEATHER_TIMEOUT_MS = 8000;
export function weatherUrl(place: WeatherPlace, units: Units): string;
export function weatherIcon(code: number, isDay: boolean): WeatherIcon;
export { WEATHER_ICON_SVG } from "./weather-icons"; // re-export (AC-015)
export type HttpGet = (url: string, timeoutMs: number) => Promise<string>;
export function xhrGet(url: string, timeoutMs: number): Promise<string>;
export function fetchWeather(place: WeatherPlace, units: Units, httpGet?: HttpGet): Promise<WeatherNow>;
```
- `weatherUrl`: `https://api.open-meteo.com/v1/forecast?latitude=<lat>&longitude=<lon>&current=temperature_2m,weather_code,is_day&timezone=auto`, plus `&temperature_unit=fahrenheit` when `units === "f"`. Numbers with `String(n)`.
- `weatherIcon` (Lightkeeper mapping): `code <= 1` → `isDay ? "sun" : "moon"`; `code <= 3` → `"cloud"`; `code <= 48` → `"fog"`; `code >= 95` → `"storm"`; `71..77`, `85`, `86` → `"snow"`; otherwise `"rain"`.
- `WEATHER_ICON_SVG` lives in **`src/weather-icons.ts` (WP-01, wave 1)** as `export const WEATHER_ICON_SVG: { readonly [icon in WeatherIcon]: string }` (pure constants, so `view.ts` can import it in wave 2 without waiting for `weather.ts`): complete `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">…</svg>` strings built from these Lightkeeper paths (trusted constants):
  - sun `<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>`
  - moon `<path d="M20 14.6A8.2 8.2 0 0 1 9.4 4 8.2 8.2 0 1 0 20 14.6z"/>`
  - cloud `<path d="M6.5 18h11a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.3 11.1 3.5 3.5 0 0 0 6.5 18z"/>`
  - fog `<path d="M4 9h16M6 13h12M4 17h16"/>`
  - rain = HIGH_CLOUD + `<path d="M8.5 18l-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5"/>`
  - snow = HIGH_CLOUD + `<path d="M8 19.5h.01M12 19.5h.01M16 19.5h.01" stroke-width="2.4"/>`
  - storm = HIGH_CLOUD + `<path d="M12.5 16l-2 3h3l-2 3"/>`
  - where HIGH_CLOUD = `<path d="M6.5 15h11a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.3 8.1 3.5 3.5 0 0 0 6.5 15z"/>`
- `xhrGet`: `XMLHttpRequest` GET with `timeout = timeoutMs`; resolves `responseText` when `status === 200`; rejects `WeatherError("HTTP <status>")` otherwise, `WeatherError("Tiempo agotado")` on timeout, `WeatherError("Sin conexión")` on `onerror`.
- `fetchWeather`: calls `(httpGet ?? xhrGet)(weatherUrl(place, units), WEATHER_TIMEOUT_MS)` once; parses `{ current: { temperature_2m: number, weather_code: number, is_day: 0|1 } }`; resolves `{ temperature: Math.round(temperature_2m), icon: weatherIcon(weather_code, is_day === 1) }`; any parse/shape problem → `WeatherError("Respuesta inválida")`; a rejection of `httpGet` that is not a `WeatherError` is wrapped in `WeatherError`.

### `view.ts` (WP-05)
```ts
export const ROW_VIEWPORT_PX = 1696;
export function computeRowOffset(itemLeft: number, itemWidth: number, viewport: number, current: number): number;
export interface LauncherView { update(model: ViewModel): void; rowOffset(): number; }
export function createLauncherView(root: HTMLElement): LauncherView;
```
- `computeRowOffset` (pure, Lightkeeper rule): `if (itemLeft + itemWidth > current + viewport) return itemLeft + itemWidth - viewport; if (itemLeft < current) return itemLeft; return current;`
- `createLauncherView` clears `root` once and builds a persistent skeleton. `update` changes only what differs; **the `[data-role="app-row"]` element and its tiles are rebuilt only when the tiles array (by reference) or `rowStyle` change**.
- DOM contract after `update(model)`:
  - `[data-role="background"]`: inline `background-image` contains `assets/backgrounds/<model.background>.jpg`.
  - `[data-role="veil"]`: inline `opacity` = `String(model.veil / 100)`.
  - `[data-role="clock-time"]` text = `clock.time`; `[data-role="clock-meridiem"]` text = `clock.meridiem` (present always; empty in 24 h); `[data-role="clock-date"]` text = `clock.date`.
  - `[data-role="weather"]` absent when `model.weather === null`; else text starts with `place`, and when `now` is set it is `<place> · <temperature>°` followed by a `[data-role="weather-icon"]` element with `data-icon="<icon>"` whose `innerHTML` is `WEATHER_ICON_SVG[icon]` imported from `./weather-icons` (constants only; never data in `innerHTML`).
  - `[data-role="app-row"]` with `data-style="<rowStyle>"`, one `[data-role="app-tile"]` per tile in order with `data-tile-id="<id>"` and `data-tile-type="<type>"`; each has `[data-role="app-title"]`. In `tiles` style each also has the v1 icon/initial (`[data-role="app-icon"]` for `app` with `icon`, else `[data-role="app-initial"]`); in `names` style neither.
  - Exactly the tile at `focusIndex` has class `is-focused` and `aria-current="true"`.
  - After each `update`, the row's inline `transform` is `translateX(-<offset>px)` (also `webkitTransform`), where `offset = computeRowOffset(tile.offsetLeft, tile.offsetWidth, ROW_VIEWPORT_PX, previousOffset)` for the focused tile; `rowOffset()` returns it (starts at 0).
  - No tiles → no row; `[data-role="empty"]` text `No hay apps configuradas`. `error !== null` → `[data-role="error"]` text = error; otherwise absent.
- `public/style.css` (WP-05): Lightkeeper layout of section 4b with Jost (`@font-face` for `assets/fonts/jost-200.woff2` 200, `jost-300.woff2` 300, `jost-500.woff2` 500, `jost.woff2` `100 900`), `font-family: Jost, sans-serif`; `[data-role="background"]` full screen `background: center / cover no-repeat #000`; veil gradient; names style rules on `[data-role="app-row"][data-style="names"]`; tiles style keeps v1 tile rules on `[data-style="tiles"]` (240×240, `#141414`, focus `scale(1.08)`); error `#ff6b6b`. Credit Lightkeeper (MIT) in a comment.

### `main.ts` (WP-06)
```ts
export const CLOCK_TICK_MS = 1000;
export const WEATHER_REFRESH_MS = 1800000;
export const INVALID_CONFIG_ERROR = "Configuración inválida";
export interface LauncherOptions {
  root: HTMLElement;
  config: unknown;                                      // raw JSON, validated with parseConfig
  now?: () => Date;                                     // default () => new Date()
  launchTile?: (tile: Tile) => Promise<void>;           // default launchTile
  launchApp?: (appId: string) => Promise<void>;         // default (id) => launchApp(id); used for LG Home
  platform?: PlatformApi | null;                        // default getPlatform()
  storage?: KeyValueStore | null;                       // default getStorage()
  fetchWeather?: (place: WeatherPlace, units: Units) => Promise<WeatherNow>; // default fetchWeather
  target?: EventTarget;                                 // default root.ownerDocument
}
export interface LauncherHandle { stop(): void; }
export function startLauncher(options: LauncherOptions): LauncherHandle;
```
- Parse with `parseConfig`; on `ConfigError` use `DEFAULT_CONFIG` and the persistent error `Configuración inválida` (rethrow anything else).
- `ViewModel.weather` = `config.weather ? { place: config.weather.name, now: <last WeatherNow or null> } : null`.
- Error precedence: while the config error is set it is the one shown; launch errors are shown only with a valid config.
- Initial focus `loadLastIndex(storage, tiles.length)`. Render through one `createLauncherView(root)`; every render calls `now()` once and uses `formatClockWithMode(now(), config.clock)`.
- Timers: clock every `CLOCK_TICK_MS`; weather when `config.weather` is set: once at start, then every `WEATHER_REFRESH_MS`. A weather success stores `now` and re-renders; a failure keeps the last value (or `null`) and shows **no** error.
- Keys (`event.keyCode` via `keyFromCode`): `null` → ignore, no `preventDefault`; every mapped key → `preventDefault()`.
  - `left`/`right` → `moveFocus`, clear a launch error (not the config error), render.
  - `up` → nothing else.
  - `down` → shares the in-flight guard with `ok`; `launchApp(LG_HOME_ID)`; on rejection error `No se pudo abrir el Home de LG`. Down, Up and Back do not clear errors.
  - `ok` → with tiles and no launch in flight: `saveLastIndex(storage, index)`, then `launchTile(tile)`; on rejection error `No se pudo abrir <title>`; a second OK while in flight is ignored.
  - `back` → `platform.platformBack()` when `platform` is not null.
- `stop()`: removes the listener, clears both intervals; later key presses, ticks and weather results have no effect.

### `entry.ts` (WP-06)
Imports `./config.json` and `startLauncher`; calls `startLauncher({ root, config })` with `root = document.getElementById("app")`; throws `Error("#app no encontrado")` if missing.

### `scripts/build.mjs` (WP-01)
`buildLauncher(outDir?)` keeps its signature and v1 steps, and instead of copying three named files it **copies the whole `public/` tree recursively** (`fs.promises.cp(PUBLIC_DIR, outDir, { recursive: true })`, Node ≥ 16.7). The esbuild call adds **`charset: "utf8"`** so Spanish text stays literal in `main.js` (`index.html` declares UTF-8). It still asserts `src/entry.ts`, `public/index.html`, `public/appinfo.json`, `public/style.css` and `public/assets/fonts/jost.woff2` exist, naming the missing one.

## 7. Conventions
- Everything in design 0001 section 7 still applies (scope `launcher/` only, TypeScript settings, `lib: ES2015` + `types: []`, no `async`/`await` in `src/` runtime code, Spanish UI text and English code, naming, overrides of the TypeScript rules for Chromium 53, commits `launcher: <summary>`).
- Error classes keep no `cause` (ES2022, outside `lib: ES2015`), as in design 0001; a wrapped error's message is copied into the new one.
- **Changed:** `localStorage` is now allowed, only through `system.ts`. `fetch` is not used; network only through `weather.ts` (`XMLHttpRequest`).
- **`innerHTML`:** allowed only in `view.ts` and only with values of `WEATHER_ICON_SVG`. Everything that comes from `config.json` or the network goes through `textContent` / `setAttribute`.
- **Attribution:** code or CSS taken from Lightkeeper carries a comment `// Adapted from Lightkeeper (MIT, © 2026 Francesco Gola), https://github.com/chiotas/lightkeeper`.
- **Assets:** never add, edit or delete files under `launcher/public/assets/`.
- Unit tests that need DOM measurements stub them with `Object.defineProperty(el, "offsetLeft", { value: n, configurable: true })` (jsdom returns 0).

## 8. Work packages and acceptance criteria
### Acceptance criteria
Design 0001's **AC-003, AC-004 and AC-005 stay in force unchanged**, with their files `launcher/acceptance/clock.test.ts`, `navigation.test.ts`, `launch.test.ts` (the test-author must not rewrite them; they answer to WP-02, nobody and WP-03 respectively). **AC-001, AC-002, AC-006, AC-007, AC-008 and AC-009 are superseded**: the test-author deletes `acceptance/apps.test.ts`, `view.test.ts`, `launcher.test.ts` and `build.test.ts` and writes the criteria below in new files.

- AC-010 · Valid configuration with defaults
  - Scenario: raw `{ "tiles": [ {"id":" netflix ","title":"Netflix"}, {"type":"input","id":"com.webos.app.hdmi1","title":"HDMI 1"}, {"type":"web","title":"El País","url":" https://elpais.com "} ] }`.
  - Action: `parseConfig(raw)`.
  - Expected: `rowStyle "names"`, `clock "24"`, `background "night"`, `veil 80`, `weather null`, `units "c"`; tiles deep-equal `[{type:"app",id:"netflix",title:"Netflix"},{type:"input",id:"com.webos.app.hdmi1",title:"HDMI 1"},{type:"web",id:"web:https://elpais.com",title:"El País",url:"https://elpais.com"}]`. A full raw with `rowStyle:"tiles", clock:"12", background:"monk", veil:0, units:"f", weather:{name:"Madrid",lat:40.4,lon:-3.7}` is returned with those values. `parseConfig(defaultConfig)` succeeds, where `import defaultConfig from "../src/config.json"` (the bundled default).
  - Must not: mutate `raw` (`raw.tiles[0].id` still `" netflix "`) or return `raw.tiles` itself.
  - Check: `launcher/acceptance/config.test.ts`
  - WP: WP-02
- AC-011 · Invalid configuration is rejected precisely
  - Scenario/Action: `parseConfig` on: `[]`; `{}`; `{tiles:[{"title":"A"}]}`; `{tiles:[{type:"web",title:"X",url:"javascript:alert(1)"}]}`; `{tiles:[{type:"tv",id:"a",title:"A"}]}`; `{tiles:[{id:"a",title:"A"},{id:"a",title:"B"}]}`; `{tiles:[],veil:101}`; `{tiles:[],veil:1.5}`; `{tiles:[],rowStyle:"grid"}`; `{tiles:[],background:"godrevy"}`; `{tiles:[],weather:{name:"X",lat:91,lon:0}}`; `{tiles:[{type:"input",id:"a",title:"A",icon:"x.png"}]}`.
  - Expected: each throws `ConfigError` (`instanceof`, `name === "ConfigError"`) with messages containing, in order: `debe ser un objeto`, `tiles debe ser un array`, `posición 0`, `posición 0`, `posición 0`, `duplicado: a`, `veil`, `veil`, `rowStyle`, `background`, `weather`, `posición 0`.
  - Must not: accept a `javascript:` URL or return a partial config.
  - Check: `launcher/acceptance/config.test.ts`
  - WP: WP-02
- AC-012 · 12-hour clock
  - Scenario: `new Date(2026,9,26,19,5)`, `new Date(2026,9,26,0,30)`, `new Date(2026,9,26,12,0)`.
  - Action: `formatClockWithMode(d, "12")` and `formatClockWithMode(d, "24")`.
  - Expected: `{time:"7:05",meridiem:"PM",date:"lunes, 26 de octubre"}`, `{time:"12:30",meridiem:"AM",…}`, `{time:"12:00",meridiem:"PM",…}`; in `"24"`: `19:05`/`00:30`/`12:00` with `meridiem ""`.
  - Must not: change `formatClock` (AC-003 still passes) or pad the 12 h hour (`07:05` is wrong).
  - Check: `launcher/acceptance/clock12.test.ts`
  - WP: WP-02
- AC-013 · Launching tiles of every type
  - Scenario: fake `ServiceBridge` recording `call(uri, payload)` that answers `{"returnValue":true}` synchronously.
  - Action: `launchTile` with an app tile `netflix`, an input tile `com.webos.app.hdmi1`, a web tile `https://elpais.com`; `launchApp("x", factory)` without params.
  - Expected: payloads parse to `{id:"netflix"}`, `{id:"com.webos.app.hdmi1"}`, `{id:"com.webos.app.browser",params:{target:"https://elpais.com"}}`; the last to exactly `{id:"x"}` (no `params` key); all URIs `luna://com.webos.applicationManager/launch`; `LG_HOME_ID === "com.webos.app.home"`.
  - Must not: send `params` for app/input tiles or call the bridge more than once per launch.
  - Check: `launcher/acceptance/tiles.test.ts`
  - WP: WP-03
- AC-014 · Platform and remembered tile
  - Scenario: an in-memory `KeyValueStore`; a store whose `getItem`/`setItem` throw; `window.webOSSystem = { platformBack: spy }`; then neither `webOSSystem` nor `PalmSystem`.
  - Action: `saveLastIndex(store, 3)` then `loadLastIndex(store, 5)`, `loadLastIndex(store, 2)`; `loadLastIndex` with values `"abc"`, `"-1"`, `"1.5"`; the throwing store; `getPlatform()` in both situations.
  - Expected: `3`; `0` (out of range); `0`, `0`, `0`; throwing store → `loadLastIndex` returns `0` and `saveLastIndex` does not throw; `getPlatform()` returns the object (calling `platformBack` reaches the spy) and then `null`.
  - Must not: throw from either storage function.
  - Check: `launcher/acceptance/system.test.ts`
  - WP: WP-03
- AC-015 · Weather from Open-Meteo
  - Scenario: `place = {name:"Madrid",lat:40.4168,lon:-3.7038}`; a stub `httpGet` recording the URL and returning `'{"current":{"temperature_2m":17.6,"weather_code":2,"is_day":1}}'`.
  - Action: `fetchWeather(place, "c", stub)`; `fetchWeather(place, "f", stub)`; stub returning `"oops"`; stub rejecting `new Error("x")`; `weatherIcon` for `(0,true)`, `(1,false)`, `(3,true)`, `(45,true)`, `(61,true)`, `(73,true)`, `(86,true)`, `(95,true)`.
  - Expected: URL `https://api.open-meteo.com/v1/forecast?latitude=40.4168&longitude=-3.7038&current=temperature_2m,weather_code,is_day&timezone=auto` (and the same + `&temperature_unit=fahrenheit`); result `{temperature:18, icon:"cloud"}`; `"oops"` → `WeatherError` `Respuesta inválida`; the rejection → `WeatherError`; icons `sun, moon, cloud, fog, rain, snow, snow, storm`; `WEATHER_ICON_SVG.sun` starts with `<svg`.
  - Must not: call `httpGet` more than once per `fetchWeather`, or use the real network.
  - Check: `launcher/acceptance/weather.test.ts`
  - WP: WP-04
- AC-016 · View: styles, background, weather and the sliding row
  - Scenario: jsdom `div`; model with 8 tiles (`app` `a0`…`a7`, titles `App 0`…), `focusIndex 0`, `rowStyle "names"`, clock `{time:"7:05",meridiem:"PM",date:"lunes, 26 de octubre"}`, weather `{place:"Madrid",now:{temperature:18,icon:"cloud"}}`, background `monk`, veil `60`, error `null`. After the first `update`, stub every tile with `offsetLeft = i*300`, `offsetWidth = 250`.
  - Action: `const v = createLauncherView(root); v.update(model)`; then `update` with `focusIndex 7` (same `tiles` array reference); then `focusIndex 0`; then a model with `rowStyle "tiles"` and a tile `{type:"app",id:"b",title:"beta",icon:"b.png"}`; then one with `weather null`, `error "Ups"`, `tiles []`.
  - Expected: background style contains `assets/backgrounds/monk.jpg`; veil opacity `0.6`; time `7:05`, meridiem `PM`; weather text starts `Madrid · 18°` with `[data-role="weather-icon"][data-icon="cloud"]` containing an `svg`; 8 tiles with `data-tile-id` a0…a7, only `a0` focused; names style has no `[data-role="app-initial"]`; the row element is the **same object** across the first three updates; after `focusIndex 7`: `rowOffset() === 7*300+250-1696` (= 654) and row `style.transform === "translateX(-654px)"` (assert only `transform`; jsdom may drop `webkitTransform`); after `focusIndex 0`: `rowOffset() === 0`; tiles style shows `img[data-role="app-icon"]` with src ending `b.png`; last model: no weather element, error text `Ups`, empty text `No hay apps configuradas`, no tiles.
  - Must not: interpret config data as HTML (title `"<b>x</b>"` creates no `b` element) or rebuild the row when only the focus changes.
  - Check: `launcher/acceptance/view2.test.ts`
  - WP: WP-05
- AC-017 · Remote control end to end
  - Scenario: jsdom document with `<div id="app">`; `startLauncher({root, config:{tiles:[{id:"a",title:"A"},{type:"web",title:"W",url:"https://w.test"},{id:"c",title:"C"}]}, now: fixed, launchTile: spy resolving, launchApp: spy resolving, platform: {platformBack: spy}, storage: memory store with "launcher.lastIndex"="1", fetchWeather: spy})`. Keys as in design 0001 (`cancelable:true`).
  - Action: check initial focus; press 39 (right), 13 (OK), 40 (down), 38 (up), 461 (back), 65.
  - Expected: initial focused tile is the web tile `W` (index 1); after 39 the tile `c` is focused; OK → `launchTile` called once with the tile `{type:"app",id:"c",title:"C"}` and the store now holds `"2"`; Down → `launchApp` called once with `"com.webos.app.home"`; Up → nothing launched; Back → `platformBack` called once; all mapped keys `defaultPrevented === true`; key 65 not prevented; `fetchWeather` never called (no `weather` in config).
  - Must not: launch on arrows/Up/Back, or call `platformBack` on any other key.
  - Check: `launcher/acceptance/remote.test.ts`
  - WP: WP-06
- AC-018 · Errors, weather refresh and stopping
  - Scenario: `vi.useFakeTimers()` **before** `startLauncher`; `now` a `vi.fn`; config `{tiles:[{id:"a",title:"A"}], weather:{name:"Madrid",lat:1,lon:2}}`; `storage: null`; each launcher started in this AC gets its own root, its own `target` (a fresh `EventTarget`, keys dispatched on it) and its own `now`/`fetchWeather`/`launchTile` spies, and **every launcher is stopped** before the final timer advance; `fetchWeather` spy resolving `{temperature:20,icon:"sun"}` the first time and rejecting afterwards.
  - Action: start; flush promises; advance `1800000` ms; flush; press OK with `launchTile` rejecting; separately start with `config: {}`; separately start with `platform: null` and press Back; finally `stop()` every handle and advance `3600000` ms.
  - Expected: after start `fetchWeather` called once with `({name:"Madrid",lat:1,lon:2}, "c")` and weather text `Madrid · 20°`; after 1800000 ms called a second time and the text still `Madrid · 20°` with no `[data-role="error"]`; rejected launch → error `No se pudo abrir A`, removed by the next arrow; `config: {}` → error `Configuración inválida`, 0 tiles, no throw, still there after an arrow; Back with `platform: null` → no throw, `defaultPrevented`; after stopping, none of the `fetchWeather` or `now` spies is called again.
  - Must not: show an error for a weather failure, or keep timers or the listener after `stop()`.
  - Check: `launcher/acceptance/remote.test.ts`
  - WP: WP-06
- AC-019 · Build ships the assets and stays Chromium 53 compatible
  - Scenario: Node environment (`// @vitest-environment node`), temporary dir.
  - Action: `await buildLauncher(tmp)` (from `../scripts/build.mjs`).
  - Expected: `index.html`, `main.js`, `style.css`, `appinfo.json`, `icon.png`, `largeIcon.png`, `assets/fonts/jost.woff2`, `assets/fonts/OFL.txt`, `assets/CREDITS.md` and `assets/backgrounds/<id>.jpg` for all 8 `BACKGROUND_IDS` exist; `appinfo.json` has `disableBackHistoryAPI: true`, `bgColor: "#000000"`, `version: "2.0.0"`, `id: "com.jagosar.minimal.launcher"`; `style.css` and `index.html` are byte-equal to `public/`; `main.js` parses with acorn `ecmaVersion: 2016, sourceType: "script"`, contains `Configuración inválida` and `api.open-meteo.com` (the real bundle, not a stub), and contains none of `padStart`, `Object.values`, `Object.entries`, `.includes(`, `fetch(`; `dist` has no `godrevy.jpg`.
  - Must not: emit ES modules (`index.html` has no `type="module"`), or ship a background outside `BACKGROUND_IDS`.
  - Check: `launcher/acceptance/build2.test.ts`
  - WP: WP-01 (build script), WP-06 (bundle entry)

### Work packages
**WP-01 · wave 1 · Types, configuration file, appinfo and build**
- Files: `launcher/src/types.ts`, `launcher/src/config.json`, `launcher/src/weather-icons.ts`, `launcher/src/apps.json` (delete it), `launcher/public/appinfo.json`, `launcher/scripts/build.mjs`.
- Implements: section 5 types and data, `WEATHER_ICON_SVG` in `weather-icons.ts` (section 6, `weather.ts` bullet; add the Lightkeeper attribution comment), `buildLauncher` change (section 6). Consumes nothing.
- ACs: AC-019.
- Patterns to mirror: `launcher/src/types.ts:1-25` (doc comments, readonly fields); `launcher/scripts/build.mjs:52-81` (`assertInputsExist`, build steps).
- Gotchas: removing `AppEntry` breaks `apps.ts`, `view.ts`, `main.ts` until wave 2–3 replace them: `typecheck` will fail in files you do not own — that is expected; only `src/types.ts` must be error-free. `fs.promises.cp` exists in Node 22. `BACKGROUND_IDS` uses `as const`; `typeof BACKGROUND_IDS[number]` is valid in TS 5.9.
- Imports/APIs: `import { cp } from "node:fs/promises"`.
- Validation: `node --check launcher/scripts/build.mjs`; `npm --prefix launcher run typecheck` with no errors in `src/types.ts`.

**WP-02 · wave 2 · Configuration parsing and 12-hour clock**
- Files: `launcher/src/config.ts`, `launcher/src/config.test.ts`, `launcher/src/apps.ts` (delete), `launcher/src/apps.test.ts` (delete), `launcher/src/clock.ts`, `launcher/src/clock.test.ts`.
- Implements: `ConfigError`, `DEFAULT_CONFIG`, `parseConfig`, `formatClockWithMode`. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-010, AC-011, AC-012 (and keeps AC-003 green).
- Patterns to mirror: `launcher/src/launch.ts:1-23` (domain error class with `name`, named constants for messages); `launcher/src/clock.ts:1-29` (pure functions, Spanish names arrays, `pad2` without `padStart`); `launcher/src/launch.test.ts:1-40` (unit test style). The v1 `apps.ts` (guards `isPlainObject`/`requireText`, `indexOf` for duplicates, messages with `posición <n>`) is deleted by this WP: its pattern is described in section 4b and in the `parseConfig` contract.
- Gotchas: `Number.isInteger` is ES2015 (allowed); `typeof x === "number" && isFinite(x)` for coordinates; compare URL prefixes with `url.toLowerCase().indexOf("https://") === 0`; membership in `BACKGROUND_IDS` with `(BACKGROUND_IDS as readonly string[]).indexOf(v) !== -1`.
- Imports: `import { BACKGROUND_IDS } from "./types"`, `import type { LauncherConfig, Tile, … } from "./types"`.
- Validation: `npm --prefix launcher test -- src/config.test.ts src/clock.test.ts`; `npm --prefix launcher run typecheck` and `npm --prefix launcher exec -- tsc -p tsconfig.test.json` (the first one stops at `tsconfig.json` while `main.ts`/`entry.ts` are broken in wave 2, so run the second explicitly) with no errors in your files.

**WP-03 · wave 2 · Launching tiles, platform and storage**
- Files: `launcher/src/launch.ts`, `launcher/src/launch.test.ts`, `launcher/src/system.ts`, `launcher/src/system.test.ts`.
- Implements: `launchApp` params, `LG_HOME_ID`, `BROWSER_ID`, `launchTile`, `system.ts`. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-013, AC-014 (and keeps AC-005 green).
- Patterns to mirror: `launcher/src/launch.ts:1-62`; `launcher/src/launch.test.ts:1-40`.
- Gotchas: declare `webOSSystem?: PlatformApi; PalmSystem?: PlatformApi;` on `Window` (only `platformBack` is used) and check `typeof obj.platformBack === "function"`. Accessing `window.localStorage` itself can throw (privacy settings): wrap the access in `try/catch` inside `getStorage`. In tests, clean up `window.webOSSystem` after each test.
- Validation: `npm --prefix launcher test -- src/launch.test.ts src/system.test.ts`; `npm --prefix launcher run typecheck` and `npm --prefix launcher exec -- tsc -p tsconfig.test.json` (the first one stops at `tsconfig.json` while `main.ts`/`entry.ts` are broken in wave 2, so run the second explicitly) with no errors in your files.

**WP-04 · wave 2 · Weather**
- Files: `launcher/src/weather.ts`, `launcher/src/weather.test.ts`.
- Implements: everything of `weather.ts` in section 6. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-015.
- Patterns to mirror: `launcher/src/launch.ts:24-62` (promise wrapper around a callback API, settle once); `launcher/src/launch.test.ts:1-40`.
- Gotchas: unit-test `xhrGet` with a fake `XMLHttpRequest` assigned to `globalThis.XMLHttpRequest` in the test (restore it afterwards); never hit the network. `is_day` arrives as `0`/`1`.
- Validation: `npm --prefix launcher test -- src/weather.test.ts`; `npm --prefix launcher run typecheck` and `npm --prefix launcher exec -- tsc -p tsconfig.test.json` (the first one stops at `tsconfig.json` while `main.ts`/`entry.ts` are broken in wave 2, so run the second explicitly) with no errors in your files.

**WP-05 · wave 2 · View and style**
- Files: `launcher/src/view.ts`, `launcher/src/view.test.ts`, `launcher/public/style.css`.
- Implements: `ROW_VIEWPORT_PX`, `computeRowOffset`, `LauncherView`, `createLauncherView`, the stylesheet. Consumes `types.ts` by implementation (WP-01) and `WEATHER_ICON_SVG` from `./weather-icons` (WP-01) by implementation. `view.ts` must not import `./weather`.
- ACs: AC-016.
- Patterns to mirror: `launcher/src/view.ts:1-54` (`createWithRole`, data-roles); `launcher/public/style.css:1-95` (Chromium 53 constraints); `launcher/src/view.test.ts:1-30`.
- Gotchas: measure the focused tile **after** inserting/updating it in the DOM; keep the previous offset in the closure. Set both `style.transform` and `style.webkitTransform`. Do not rebuild the row on focus changes, or the slide transition never animates. Absolute positioning per section 4b, body fixed at 1920×1080.
- Validation: `npm --prefix launcher test -- src/view.test.ts`; `npm --prefix launcher run typecheck` and `npm --prefix launcher exec -- tsc -p tsconfig.test.json` (the first one stops at `tsconfig.json` while `main.ts`/`entry.ts` are broken in wave 2, so run the second explicitly) with no errors in your files.

**WP-06 · wave 3 · Wiring and entry point**
- Files: `launcher/src/main.ts`, `launcher/src/main.test.ts`, `launcher/src/entry.ts`.
- Implements: `main.ts` and `entry.ts` of section 6. Consumes by implementation `parseConfig`, `ConfigError`, `DEFAULT_CONFIG`, `formatClockWithMode` (WP-02), `launchTile`, `launchApp`, `LG_HOME_ID`, `getPlatform`, `getStorage`, `loadLastIndex`, `saveLastIndex` (WP-03), `fetchWeather` (WP-04), `createLauncherView` (WP-05), and `keyFromCode`, `moveFocus` (unchanged `navigation.ts`).
- ACs: AC-017, AC-018, AC-019 (shared with WP-01: the bundle starts at your `entry.ts`), and AC-004 (frozen `navigation.ts`, which you consume: if it fails, report it, do not edit `navigation.ts`).
- Patterns to mirror: `launcher/src/main.ts:1-96`; `launcher/src/main.test.ts:1-40`; `launcher/acceptance/clock.test.ts:1-43` (acceptance test style; the v1 `launcher.test.ts` is deleted in wave 0).
- Gotchas: if `launcher/public/assets/fonts/jost.woff2` is missing, the build fails with "Missing build input": that is the architect's asset gate, report it as a blocker. Pass the same `config.tiles` reference to every `view.update` (the view rebuilds the row only when it changes). Weather results can arrive after `stop()`: check `isStopped` before rendering. Count `now()` calls only through renders (AC-018). Default `launchApp` option must call `launchApp(id)` with no factory/params.
- Validation: `npm --prefix launcher test`; `npm --prefix launcher run typecheck`; `npm --prefix launcher run build`; `npm --prefix launcher run acceptance`.

```json wp-manifest
{
  "stack": ["typescript"],
  "allow_bash": [
    "Bash(npm --prefix launcher install:*)",
    "Bash(npm --prefix launcher test:*)",
    "Bash(npm --prefix launcher run build:*)",
    "Bash(npm --prefix launcher run typecheck:*)",
    "Bash(npm --prefix launcher run acceptance:*)",
    "Bash(npm --prefix launcher run qa:*)",
    "Bash(node --check launcher/scripts/build.mjs)",
    "Bash(npm --prefix launcher exec -- tsc -p tsconfig.test.json)"
  ],
  "acceptance": {"files": ["launcher/acceptance/"], "command": "npm --prefix launcher run acceptance"},
  "verify": {
    "build": "npm --prefix launcher run build",
    "types": "npm --prefix launcher run typecheck",
    "tests": "npm --prefix launcher test"
  },
  "qa": {"files": ["launcher/qa/"]},
  "docs": {"files": ["launcher/README.md"], "codemap": null},
  "ignore": ["launcher/node_modules/", "launcher/dist/"],
  "work_packages": [
    {"id": "WP-01", "wave": 1, "depends_on": [],
     "files": ["launcher/src/types.ts", "launcher/src/config.json", "launcher/src/weather-icons.ts", "launcher/src/apps.json", "launcher/public/appinfo.json", "launcher/scripts/build.mjs"],
     "acs": ["AC-019"],
     "mirror": ["launcher/src/types.ts:1-25", "launcher/scripts/build.mjs:52-81"]},
    {"id": "WP-02", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/config.ts", "launcher/src/config.test.ts", "launcher/src/apps.ts", "launcher/src/apps.test.ts", "launcher/src/clock.ts", "launcher/src/clock.test.ts"],
     "acs": ["AC-003", "AC-010", "AC-011", "AC-012"],
     "mirror": ["launcher/src/launch.ts:1-23", "launcher/src/clock.ts:1-29", "launcher/src/launch.test.ts:1-40"]},
    {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/launch.ts", "launcher/src/launch.test.ts", "launcher/src/system.ts", "launcher/src/system.test.ts"],
     "acs": ["AC-005", "AC-013", "AC-014"],
     "mirror": ["launcher/src/launch.ts:1-62", "launcher/src/launch.test.ts:1-40"]},
    {"id": "WP-04", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/weather.ts", "launcher/src/weather.test.ts"],
     "acs": ["AC-015"],
     "mirror": ["launcher/src/launch.ts:24-62", "launcher/src/launch.test.ts:1-40"]},
    {"id": "WP-05", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/view.ts", "launcher/src/view.test.ts", "launcher/public/style.css"],
     "acs": ["AC-016"],
     "mirror": ["launcher/src/view.ts:1-54", "launcher/public/style.css:1-95", "launcher/src/view.test.ts:1-30"]},
    {"id": "WP-06", "wave": 3, "depends_on": ["WP-02", "WP-03", "WP-04", "WP-05"],
     "files": ["launcher/src/main.ts", "launcher/src/main.test.ts", "launcher/src/entry.ts"],
     "acs": ["AC-004", "AC-017", "AC-018", "AC-019"],
     "mirror": ["launcher/src/main.ts:1-96", "launcher/src/main.test.ts:1-40", "launcher/acceptance/clock.test.ts:1-43"]}
  ]
}
```

## 9. Test strategy
- **Unit:** each WP rewrites or adds `src/<module>.test.ts`; doubles in the test file; fake timers and DOM measurement stubs as in section 7. `navigation.test.ts` stays as is.
- **Acceptance:** in wave 0 the test-author keeps `clock.test.ts`, `navigation.test.ts`, `launch.test.ts`, deletes `apps.test.ts`, `view.test.ts`, `launcher.test.ts`, `build.test.ts`, and writes `config.test.ts`, `clock12.test.ts`, `tiles.test.ts`, `system.test.ts`, `weather.test.ts`, `view2.test.ts`, `remote.test.ts`, `build2.test.ts`, one `describe` per AC titled with its id. Red before the code (imports of new modules fail).
- **QA:** rewrites `launcher/qa/` for v2 (the v1 QA files use removed contracts): the built bundle in jsdom with a fake `PalmServiceBridge`, `webOSSystem` and `XMLHttpRequest`; long rows (the d1 scenario, now expected to pass); weather failures; CSS review against section 4b (no `gap`, no `inset`, transform/opacity transitions only); assets present and licensed in `assets/CREDITS.md`.
- All from the repo root; no real network in any test.

## 10. Decisions
| Decision | Discarded alternative | Reason |
|---|---|---|
| Take only Lightkeeper ideas that work without root (user) | Port Lightkeeper whole | It needs root, Homebrew Channel and GlassHouse; design 0001 targets Developer Mode. |
| Sliding row, names style, picture + veil + weather, remote shortcuts and input/web tiles (user) | A subset | User chose all four groups. |
| One bundled `config.json` (user) | On-TV settings panel; phone settings server | No root for a server; a panel is a lot more work. |
| `names` as default style (user) | Square tiles by default | Lightkeeper's calmer look; tiles stay as an option. |
| Back calls `platformBack()` (user) — **changes design 0001**, where Back was swallowed | Keep swallowing Back | Matches Lightkeeper and webOS expectations; needs `disableBackHistoryAPI`. |
| Fixed background from config (user) | Random at start | Simpler, deterministic. |
| Bundle free assets: Jost (OFL) and PD/CC0 paintings + Lightkeeper's MIT gradients (user) | CSS-only backgrounds | User wants the pictures; licences allow it. The Godrevy photo (rights reserved) and brand logos are excluded. |
| The architect copies the binary assets before `/implement` (architect) | An implementer copying them | Agents cannot author binaries and their allowed commands cannot reach files outside the repo. |
| `localStorage` allowed via `system.ts` — **changes design 0001** (architect) | No persistence | Needed for "remember last tile"; isolated and failure-tolerant. |
| Weather via `XMLHttpRequest` with timeout, refresh 30 min, silent failure (architect) | `fetch`; showing errors | XHR has a native timeout on Chromium 53; weather is decoration, not a reason to show an error. |
| View with a persistent skeleton and incremental updates (architect) | Re-render everything each second (v1) | Rebuilding every second kills the slide transition and re-measures the row needlessly. |
| `launchApp` gains an optional third `params` argument; new `launchTile` (architect) | Changing `launchApp`'s second parameter | Keeps AC-005 valid. |
| Keep AC-003/004/005 and their files; supersede the rest with AC-010…019 (architect) | Renumber everything from AC-001 | Unchanged behaviour keeps its tests; new numbers avoid collisions with design 0001's archived ACs. |
| Waves 1 → 2 (4 WPs in parallel) → 3 (architect) | All in one wave | Wave 2 needs the new `types.ts`; WP-06 integrates the four real modules and the bundle. `WEATHER_ICON_SVG` lives in WP-01's `weather-icons.ts`, so WP-05 consumes it by implementation and stays in wave 2. |
| Inputs launched as apps (`com.webos.app.hdmiN`, `com.webos.app.livetv`) (architect) | A separate input-switch service | Lightkeeper does the same (`logos.js:212`, launch by id); no extra API. |

## 11. Risks and open questions
- **Target device (from the user):** LG OLED55C9PLA (C9, manufactured 12/2019), which ships with webOS 4.5 — consistent with the Chromium 53 target. Remote: a **One For All IR universal remote** (no Magic Remote pointer). The design is key-only, so nothing depends on a pointer; the remote has arrows, OK, BACK, EXIT, Home, number and coloured keys. Only arrows, OK, BACK (461) and Down are used; Home and EXIT stay with the system. The keyCodes the One For All sends through webOS are assumed equal to LG's own remote (not verified).
- **Uncommitted v1:** `launcher/` (design 0001's code) is not committed; the user chose to commit later. If this run goes wrong there is no git baseline to return to. **Rollback:** before `/implement`, copy `launcher/` aside or commit it; to roll back, restore that copy (or `git checkout`) and the archived `docs/architecture/0001-…` design.
- **Back key:** `platformBack()` and `disableBackHistoryAPI` behaviour on webOS 4 comes from Lightkeeper's code (reported working on webOS 4.4.3 with root); untested here without root.
- **`com.webos.app.home` from a Developer Mode app** and HDMI input ids are unverified on this TV; a failure only shows `No se pudo abrir …`.
- **Open-Meteo from the TV:** HTTPS from Chromium 53 to a modern TLS endpoint may fail (old root certificates); then only the city name is shown. Open-Meteo data is CC BY 4.0: the README must credit it.
- **Bundle size:** the 8 backgrounds add ≈ 2 MB to the `.ipk`; acceptable for a TV app.
- **Assets gate** (architect's checklist: copy **only** the 8 `BACKGROUND_IDS` JPGs, never `godrevy.jpg`; the build copies `public/` as is): `/implement` must not start until `launcher/public/assets/` exists with every file of section 4 and `CREDITS.md`; the architect copies them right after approval and checks the list. If missing, WP-01's build and AC-019 fail with "Missing build input".
- **Licences:** `assets/CREDITS.md` must list Jost (OFL 1.1), each painting's source/licence and Lightkeeper's MIT notice; the README (doc-updater) links to it.
- Default app ids (`netflix`, `youtube.leanback.v4`, `amazon`) remain unverified, as in design 0001.
- No real-device test; jsdom does not lay out, so the slide is tested with stubbed measurements.
