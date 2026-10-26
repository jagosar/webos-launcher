# Architecture: Minimal webOS 4 launcher
Type: feature · Size: standard

## 1. Goal
Build a minimalist launcher app for an LG OLED TV running webOS 4: a full-screen, true-black page showing the time and date and a single row of large app tiles, navigated with the remote's d-pad and opened with OK. It is a regular web app installed in Developer Mode (no root), so it does not replace the system home: it is an app the user opens, which then launches other apps through the public `luna://com.webos.applicationManager/launch` service. The list of apps it shows comes from a bundled JSON file, because a non-rooted app cannot reliably list the installed apps. The build produces a folder ready for `ares-package`.

## 2. Scope
### Included
- Clock (24 h `HH:MM`) and date in Spanish (`lunes, 26 de octubre`), refreshed every second.
- One horizontal row of app tiles from `launcher/src/apps.json`; focus moves with Left/Right, OK launches the focused app.
- Launching through `PalmServiceBridge` → `luna://com.webos.applicationManager/launch`.
- Visible error messages for an invalid app list and for a failed launch.
- Build to a webOS-ready folder (`index.html`, `main.js`, `style.css`, `appinfo.json`, `icon.png`, `largeIcon.png`), compatible with the webOS 4 web engine (Chromium 53).
### Excluded (non-goals)
- Replacing the system home/launcher, root, Homebrew Channel, reading the installed apps from the system.
- Favourites, reordering, hiding apps, settings screen, themes, dynamic backgrounds, persistence (`localStorage`).
- Up/Down navigation, multiple rows, pointer (Magic Remote cursor) support beyond what the browser gives for free.
- Running `ares-package` / `ares-install` or testing on a real TV (done by the user).
- Any change outside `launcher/` (the rest of this repo is the agentic-flow framework).

## 3. Stack
- Language: TypeScript **5.9.3** (`strict`), compiled and bundled by **esbuild 0.28.2** to a single IIFE script with `target: ["chrome53"]`. No UI framework.
- Runtime on the TV: webOS 4 web engine (Chromium 53): **no ES modules, no async/await at runtime, no `String.prototype.padStart`, no `Object.values/entries`**. esbuild lowers syntax; APIs are guarded by the TypeScript `lib` (section 7).
- Tooling runtime: Node **22.x** (≥ 22.12, required by Vitest 5), npm 10.
- Tests: **Vitest 5.0.5** with **jsdom 30.1.3**; **acorn 8.19.0** (syntax check of the bundle); **@types/node 22.20.5**.
- Package manager: npm, with `launcher/package-lock.json` committed.
- Commands (all from the repo root):
  - install: `npm --prefix launcher install`
  - build: `npm --prefix launcher run build` → `launcher/dist/`
  - types: `npm --prefix launcher run typecheck`
  - unit tests: `npm --prefix launcher test`
  - acceptance: `npm --prefix launcher run acceptance`
  - lint: not applicable (no linter in this project; `tsc --strict` is the gate).

## 4. Repository structure
Only `launcher/` is new. Owner in brackets.

```
launcher/
  package.json                 [WP-01]
  .gitignore                   [WP-01]
  package-lock.json            [WP-01]
  tsconfig.json                [WP-01]
  tsconfig.test.json           [WP-01]
  vitest.config.ts             [WP-01]
  scripts/build.mjs            [WP-01]
  public/index.html            [WP-01]
  public/appinfo.json          [WP-01]
  public/style.css             [WP-04]
  src/types.ts                 [WP-01]
  src/apps.json                [WP-01]
  src/apps.ts                  [WP-02]
  src/apps.test.ts             [WP-02]
  src/clock.ts                 [WP-02]
  src/clock.test.ts            [WP-02]
  src/navigation.ts            [WP-03]
  src/navigation.test.ts       [WP-03]
  src/launch.ts                [WP-03]
  src/launch.test.ts           [WP-03]
  src/view.ts                  [WP-04]
  src/view.test.ts             [WP-04]
  src/main.ts                  [WP-05]
  src/main.test.ts             [WP-05]
  src/entry.ts                 [WP-05]
  acceptance/                  [AT]
  qa/                          [QA]
  README.md                    [DOCS]
  node_modules/, dist/         (ignored, generated)
```

## 4b. Existing code context
The repo already has code, but none of it is related: it is the agentic-flow framework (Python in `.claude/bin/`, workflow JS in `.claude/workflows/`). There is no TypeScript, no web UI and no package.json anywhere, so there are **no patterns to mirror**; the conventions for the launcher are defined from scratch in section 7.

| What an implementer needs to know | Where it is | Pattern or rule |
|---|---|---|
| The launcher is a self-contained project | `launcher/` (new) | Never create, edit or delete files outside `launcher/`. |
| Framework files are protected | `.claude/`, `architecture.md`, `docs/` | Do not touch them; the hook blocks `.claude/`. |
| Root `README.md`, `docs/` belong to the framework | `README.md`, `docs/how-it-works.md` | Not part of this project. The launcher's docs go in `launcher/README.md` (doc-updater). |
| Commands run from the repo root | — | Always `npm --prefix launcher …`; never `cd`. Paths inside `scripts/build.mjs` are resolved from the script's own location, not from `process.cwd()`. |
| Entry point on the TV | `launcher/public/appinfo.json` → `"main": "index.html"` | `index.html` loads `style.css` and `main.js` with a classic `<script>` (no `type="module"`). |
| Data flow | `src/apps.json` → `parseAppList` → `startLauncher` state → `renderLauncher` DOM; keydown → `keyFromCode` → `moveFocus` / `launchApp` | One-way: state changes only in `main.ts`; `view.ts` only renders. |
| State changes | `main.ts` only | Focus index and the current error message; nothing is persisted. |
| External contract | webOS Luna bus via `window.PalmServiceBridge` | URI `luna://com.webos.applicationManager/launch`, payload `{"id": "<appId>"}`, response JSON with `returnValue` (boolean) and, on failure, `errorText`. |

## 5. Data model
`launcher/src/types.ts` (WP-01), the only shared types:

```ts
/** One app shown in the row. `icon` is an optional URL or path relative to index.html. */
export interface AppEntry {
  readonly id: string;     // webOS app id, e.g. "netflix"
  readonly title: string;  // visible name
  readonly icon?: string;
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

/** Minimal shape of webOS's PalmServiceBridge. */
export interface ServiceBridge {
  onservicecallback: ((message: string) => void) | null;
  call(uri: string, payload: string): void;
}
```

`launcher/src/apps.json` (WP-01): a JSON array of `AppEntry` objects. Default content, in this order (ids to be confirmed by the user, see section 11):

```json
[
  { "id": "com.webos.app.livetv", "title": "TV" },
  { "id": "youtube.leanback.v4", "title": "YouTube" },
  { "id": "netflix", "title": "Netflix" },
  { "id": "amazon", "title": "Prime Video" },
  { "id": "com.webos.app.browser", "title": "Navegador" }
]
```

`launcher/public/appinfo.json` (WP-01):

```json
{
  "id": "com.jagosar.minimal.launcher",
  "version": "0.1.0",
  "vendor": "jagosar",
  "type": "web",
  "main": "index.html",
  "title": "Minimal Launcher",
  "icon": "icon.png",
  "largeIcon": "largeIcon.png",
  "resolution": "1920x1080"
}
```

## 6. Contracts between modules
All modules are ES modules in `launcher/src/`, imported with relative paths without extension (e.g. `import { parseAppList } from "./apps"`).

### `apps.ts` (WP-02)
```ts
export class AppListError extends Error { name = "AppListError"; }
export function parseAppList(raw: unknown): AppEntry[];
```
- Returns a **new** array of new objects `{ id, title }` plus `icon` only when present, in input order. Does not mutate `raw`.
- `id` and `title` are trimmed; `icon`, if present, must be a non-empty string.
- Throws `AppListError` when:
  - `raw` is not an array → message `"La lista de apps debe ser un array"`;
  - an element is not an object, or its `id`/`title` is missing, not a string or empty after trim → message contains `"posición <index>"` (0-based index);
  - `icon` is present and not a non-empty string → message contains `"posición <index>"`;
  - an `id` repeats → message contains `"duplicado: <id>"`.
- An empty array is valid and returns `[]`.

### `clock.ts` (WP-02)
```ts
export function formatClock(now: Date): ClockText;
```
- Uses local time (`getHours`, `getMinutes`, `getDay`, `getDate`, `getMonth`). Must not use `Intl`, `toLocaleString` or `padStart`.
- `time`: `HH:MM`, 24 h, both zero-padded to 2 digits.
- `date`: `<weekday>, <day> de <month>`, day without padding. Names (index = `getDay()` / `getMonth()`):
  - weekdays: `domingo, lunes, martes, miércoles, jueves, viernes, sábado`
  - months: `enero, febrero, marzo, abril, mayo, junio, julio, agosto, septiembre, octubre, noviembre, diciembre`

### `navigation.ts` (WP-03)
```ts
export const KEY_CODES: { readonly [code: number]: NavKey };
export function keyFromCode(keyCode: number): NavKey | null;
export function moveFocus(state: FocusState, key: NavKey): FocusState;
```
- `KEY_CODES`: `37→"left"`, `38→"up"`, `39→"right"`, `40→"down"`, `13→"ok"`, `461→"back"` (webOS Back), `27→"back"` (Escape, for desktop testing).
- `keyFromCode` returns the mapped key or `null`; test membership with `Object.prototype.hasOwnProperty.call(KEY_CODES, keyCode)` (the index type always looks like `NavKey`).
- `moveFocus` always returns a new object: `left` → `max(index-1, 0)`; `right` → `min(index+1, count-1)`; any other key → same index. No wrap-around. With `count === 0` the result is `{ index: 0, count: 0 }`.

### `launch.ts` (WP-03)
```ts
export class LaunchError extends Error { name = "LaunchError"; }
export const LAUNCH_URI = "luna://com.webos.applicationManager/launch";
export function defaultBridgeFactory(): ServiceBridge;
export function launchApp(appId: string, bridgeFactory?: () => ServiceBridge): Promise<void>;
```
- `defaultBridgeFactory` returns `new window.PalmServiceBridge()`; if `window.PalmServiceBridge` is undefined it throws `LaunchError("PalmServiceBridge no disponible")`.
- `launchApp`:
  1. creates one bridge with `bridgeFactory ?? defaultBridgeFactory` (a throw becomes a rejection with that `LaunchError`);
  2. sets `onservicecallback` **before** calling;
  3. calls `bridge.call(LAUNCH_URI, JSON.stringify({ id: appId }))` exactly once;
  4. on the callback: parses the JSON; `returnValue === true` → resolve; otherwise reject `LaunchError(errorText ?? "Error desconocido")`; unparsable JSON → reject `LaunchError("Respuesta inválida")`. Only the first callback counts.
- Written with `new Promise(...)` and callbacks, no `async`/`await`.

### `view.ts` (WP-04)
```ts
export function renderLauncher(
  root: HTMLElement,
  apps: readonly AppEntry[],
  focus: FocusState,
  clock: ClockText,
  error: string | null
): void;
```
Replaces the whole content of `root` on each call (idempotent). DOM contract (used by tests and CSS):
- `[data-role="clock-time"]` text = `clock.time`; `[data-role="clock-date"]` text = `clock.date`.
- `[data-role="app-row"]` contains one `[data-role="app-tile"]` per app, in order, each with `data-app-id="<id>"`, containing `[data-role="app-title"]` (text = title) and either `<img data-role="app-icon" src="<icon>" alt="">` when `icon` is set, or `[data-role="app-initial"]` with the first character of the title upper-cased.
- Exactly the tile at `focus.index` has class `is-focused` and `aria-current="true"`; the others have neither.
- With `apps.length === 0`: no tiles; `[data-role="empty"]` with text `No hay apps configuradas`.
- With `error !== null`: `[data-role="error"]` with text = `error`; with `null`, no such element.
- Text is set with `textContent`, never `innerHTML` with data.

### `main.ts` (WP-05)
```ts
export const CLOCK_TICK_MS = 1000;
export interface LauncherOptions {
  root: HTMLElement;
  apps: unknown;                              // raw JSON, validated with parseAppList
  now?: () => Date;                           // default () => new Date()
  launch?: (appId: string) => Promise<void>;  // default launchApp
  target?: EventTarget;                       // where keydown is listened; default root.ownerDocument
}
export interface LauncherHandle { stop(): void; }
export function startLauncher(options: LauncherOptions): LauncherHandle;
```
- Parses `apps`; on `AppListError` it renders with `apps = []` and error `Lista de apps inválida` (and does not rethrow).
- Initial focus `{ index: 0, count: apps.length }`; renders immediately, then re-renders the clock every `CLOCK_TICK_MS` with `setInterval`.
- On `keydown` on `target`: `keyFromCode(event.keyCode)`:
  - `null` → ignore (no `preventDefault`);
  - `left`/`right`/`up`/`down` → `moveFocus`, clear a launch error (the `Lista de apps inválida` error stays for the whole session), re-render, `preventDefault()`;
  - `ok` with at least one app → clear the error, call `launch(focusedApp.id)` once; on rejection set error `No se pudo abrir <title>` and re-render; `preventDefault()`;
  - `back` → `preventDefault()` only (the launcher does not exit or navigate).
- Every render calls `now()` once and formats it with `formatClock` (key re-renders included).
- While a launch is in flight (until its promise settles), further `ok` presses are ignored (still `preventDefault()`), so one OK = one launch.
- `ok` with zero apps: `preventDefault()` and nothing else.
- `stop()` removes the listener and clears the interval; after it, keys and ticks have no effect.

### `entry.ts` (WP-05)
Bundle entry: imports `./apps.json` and calls `startLauncher({ root, apps })` with `root = document.getElementById("app")`; if missing, throws `Error("#app no encontrado")`. No exports.

### `scripts/build.mjs` (WP-01)
```js
export async function buildLauncher(outDir?: string): Promise<void>; // default: <launcher>/dist
```
- Resolves `launcher/` from `import.meta.url`. Creates `outDir` (recursive) and writes:
  - `main.js`: esbuild bundle of `src/entry.ts`, `bundle: true`, `format: "iife"`, `target: ["chrome53"]`, `minify: true`;
  - copies of `public/index.html`, `public/appinfo.json`, `public/style.css`;
  - `icon.png` (80×80) and `largeIcon.png` (130×130): valid solid-colour PNGs (`#111111`) generated with `node:zlib` (no binary files in the repo).
- When run directly (`node scripts/build.mjs`) builds to the default and exits 0; on error prints the message and exits 1.
- `index.html`: `<!doctype html>`, `<meta charset="utf-8">`, `<link rel="stylesheet" href="style.css">`, `<body><div id="app"></div><script src="main.js"></script></body>`.

## 7. Conventions
- **Scope:** only files inside `launcher/`, and only the ones your WP owns.
- **TypeScript:** `strict: true`, `noEmit`, `target: "ES2015"`, `module: "ESNext"`, `moduleResolution: "Bundler"`, `resolveJsonModule: true`, `skipLibCheck: true`, **`lib: ["ES2015", "DOM", "DOM.Iterable"]`** and **`types: []`** (this is what keeps out APIs that Chromium 53 lacks: `padStart`, `Object.values/entries`, `Array.prototype.includes`; use `indexOf` and manual padding). Without `types: []`, `@types/node` and vitest's types would load newer libs and silently re-enable those APIs. `include: ["src"]`, `exclude: ["src/**/*.test.ts"]`. A second `tsconfig.test.json` (`extends: "./tsconfig.json"`, `lib: ["ES2022", "DOM", "DOM.Iterable"]`, `types: ["node"]`, `include: ["src/**/*.test.ts"]`, `exclude: []`) type-checks the unit tests. Add `declare global { interface Window { PalmServiceBridge?: new () => ServiceBridge } }` in `launch.ts`.
- **Runtime code:** no `async`/`await`, no ES module loading at runtime, no `fetch` (the app list is bundled), no external CDN, no `localStorage`.
- **Naming:** files `lowerCamel`/single word `.ts`; functions `camelCase`; types `PascalCase`; constants `UPPER_SNAKE_CASE`. User-visible text in Spanish, code and comments in English.
- **Errors:** domain errors are `AppListError` and `LaunchError` (section 6), with the exact messages listed there. No `console.log` left in `src/`.
- **Immutability:** pure functions return new objects; only `main.ts` holds mutable state.
- **Tests:** next to the code (`src/x.test.ts`), `import { describe, it, expect, vi } from "vitest"`; jsdom is the default environment (`vitest.config.ts`); a test that needs Node APIs starts with `// @vitest-environment node`. No real network, no real timers in assertions (`vi.useFakeTimers()`).
- **Styling (minimalist, OLED-friendly):** background `#000`; text `#f2f2f2`; one sans-serif family (`"LG Smart UI", "Helvetica Neue", Arial, sans-serif`); 5% safe-area padding; clock top-left, time ~96px weight 200, date ~28px at 60% opacity; tiles 240×240px with 32px gap and 24px radius, background `#141414`, no shadows; focused tile `transform: scale(1.08)` and a 3px `#f2f2f2` outline, `transition: transform 150ms ease-out` (transform only). Errors and empty message: 24px, `#ff6b6b` for the error.
- **Overrides of `.claude/rules/agentic/typescript.md`** (Chromium 53 runtime): use `Promise` `.then/.catch` instead of `async`/`await` + `try/catch` in `src/` runtime code (tests may use `async`/`await`); no `structuredClone` (copy with object spread / `slice`); error classes without `cause` (ES2022).
- `npm audit` (asked by the TypeScript rules when the lockfile changes) is waived: dev-only dependencies, nothing ships but the bundle.
- **Commits:** `launcher: <imperative summary>`; one commit per logical block. `node_modules/` and `dist/` are never committed (`launcher/.gitignore`).

## 8. Work packages and acceptance criteria
### Acceptance criteria
- AC-001 · Valid app list is parsed in order
  - Scenario: raw `[{"id":" netflix ","title":"Netflix"},{"id":"youtube.leanback.v4","title":"YouTube","icon":"yt.png"}]`.
  - Action: `parseAppList(raw)`.
  - Expected: `[{id:"netflix",title:"Netflix"},{id:"youtube.leanback.v4",title:"YouTube",icon:"yt.png"}]` (deep equal, the first has no `icon` key); `parseAppList([])` → `[]`.
  - Must not: return the same array or mutate `raw` (after the call `raw[0].id === " netflix "` and the result `!== raw`).
  - Check: `launcher/acceptance/apps.test.ts`
  - WP: WP-02
- AC-002 · Invalid app lists are rejected with a precise error
  - Scenario: `{}`; `[{"id":"a"}]`; `[{"id":"a","title":"A"},{"id":"a","title":"B"}]`; `[{"id":"a","title":"A","icon":""}]`.
  - Action: `parseAppList` on each.
  - Expected: each throws `AppListError` (`instanceof` and `name === "AppListError"`); messages contain respectively `debe ser un array`, `posición 0`, `duplicado: a`, `posición 0`.
  - Must not: return a partial list or throw a generic `Error`/`TypeError`.
  - Check: `launcher/acceptance/apps.test.ts`
  - WP: WP-02
- AC-003 · Clock text in Spanish, 24 h
  - Scenario: `new Date(2026, 9, 26, 7, 5)` and `new Date(2026, 0, 4, 23, 59)`.
  - Action: `formatClock(date)`.
  - Expected: `{time:"07:05", date:"lunes, 26 de octubre"}` and `{time:"23:59", date:"domingo, 4 de enero"}`.
  - Must not: depend on the machine locale (the test also runs with `Intl` methods stubbed to throw: `vi.spyOn(Date.prototype, "toLocaleDateString")` and `toLocaleTimeString` throwing; results unchanged).
  - Check: `launcher/acceptance/clock.test.ts`
  - WP: WP-02
- AC-004 · Remote keys and focus movement
  - Scenario: `{index:0,count:3}`.
  - Action: `keyFromCode` for 37, 38, 39, 40, 13, 461, 27, 65; `moveFocus` with `left`, `right` ×3, `up`, `down`; and `moveFocus({index:0,count:0}, "right")`.
  - Expected: keys `left, up, right, down, ok, back, back, null`; left from 0 → 0; right ×3 → 1, 2, 2; up/down keep the index; empty row → `{index:0,count:0}`.
  - Must not: wrap around (right from 2 is never 0) or mutate the input state (input object unchanged and result `!==` input).
  - Check: `launcher/acceptance/navigation.test.ts`
  - WP: WP-03
- AC-005 · Launching through the Luna bus
  - Scenario: a fake `ServiceBridge` that records `call(uri, payload)` and lets the test fire `onservicecallback`.
  - Action: `launchApp("netflix", () => fake)`, then callback `'{"returnValue":true}'`; second run with `'{"returnValue":false,"errorText":"App no instalada"}'`; third with `"not json"`; fourth `launchApp("x")` with `window.PalmServiceBridge` undefined.
  - Expected: `call` invoked once with `"luna://com.webos.applicationManager/launch"` and a payload that `JSON.parse`s to `{id:"netflix"}`; first resolves; second rejects `LaunchError` with message `App no instalada`; third rejects `LaunchError` `Respuesta inválida`; fourth rejects `LaunchError` `PalmServiceBridge no disponible`.
  - Must not: resolve before the callback (the promise is still pending after `await Promise.resolve()` when no callback has fired) or call the bridge more than once.
  - Check: `launcher/acceptance/launch.test.ts`
  - WP: WP-03
- AC-006 · Rendering the launcher
  - Scenario: jsdom `div`; apps `[{id:"a",title:"alpha"},{id:"b",title:"Beta",icon:"b.png"},{id:"c",title:"Gamma"}]`, focus `{index:1,count:3}`, clock `{time:"07:05",date:"lunes, 26 de octubre"}`, error `null`.
  - Action: `renderLauncher(root, apps, focus, clock, null)`, then again with focus `{index:2,count:3}` and error `"Ups"`; then with `[]`.
  - Expected: 3 `[data-role="app-tile"]` with `data-app-id` a, b, c; only `b` has `is-focused` and `aria-current="true"`; tile a shows initial `A`, tile b an `img[data-role="app-icon"]` with `src` ending `b.png`; clock texts match; after the second call still 3 tiles (not 6), only `c` focused, `[data-role="error"]` text `Ups`; with `[]`: 0 tiles and `[data-role="empty"]` text `No hay apps configuradas`.
  - Must not: interpret data as HTML (a title `"<b>x</b>"` produces no `b` element and its text is literally `<b>x</b>`).
  - Check: `launcher/acceptance/view.test.ts`
  - WP: WP-04
- AC-007 · Navigating and launching from the remote
  - Scenario: jsdom document with `<div id="app">`; `startLauncher({root, apps:[{id:"a",title:"A"},{id:"b",title:"B"}], now: () => new Date(2026,9,26,7,5), launch: spy resolving})`. Keys are dispatched on `document` as `keydown` events whose `keyCode` is set (create with `new KeyboardEvent("keydown",{keyCode, cancelable:true, bubbles:true})` — without `cancelable` `preventDefault()` is a no-op — and, to be safe, `Object.defineProperty(ev,"keyCode",{value:keyCode})`).
  - Action: press 39 (right), then 13 (OK).
  - Expected: after 39, tile `b` is `is-focused`; after 13, `launch` called exactly once with `"b"`; the clock shows `07:05`; both events have `defaultPrevented === true`.
  - Must not: call `launch` on arrow keys, or on key 65 (which is not `defaultPrevented`).
  - Check: `launcher/acceptance/launcher.test.ts`
  - WP: WP-05
- AC-008 · Errors and stopping
  - Scenario: as AC-007 but `launch` rejects; separately `apps: {}`; separately a handle that is then stopped, with `vi.useFakeTimers()` called **before** `startLauncher` and `now` a `vi.fn`.
  - Action: press 13; start with invalid apps; `stop()` then press 13 and advance timers 5000 ms.
  - Expected: after the rejection settles, `[data-role="error"]` text `No se pudo abrir A`, and a following arrow key removes it; invalid apps → `[data-role="error"]` text `Lista de apps inválida` and 0 tiles, still shown after pressing 39, `startLauncher` does not throw; after `stop()`, `launch` is not called and `now` is not called again.
  - Must not: throw from `startLauncher` or leave the listener or interval active after `stop()`.
  - Check: `launcher/acceptance/launcher.test.ts`
  - WP: WP-05
- AC-009 · Build output is installable on webOS 4
  - Scenario: Node environment (`// @vitest-environment node`), a temporary directory from `fs.mkdtempSync(path.join(os.tmpdir(), "launcher-"))`.
  - Action: `import { buildLauncher } from "../scripts/build.mjs"`; `await buildLauncher(tmp)`.
  - Expected: files `index.html`, `main.js`, `style.css`, `appinfo.json`, `icon.png`, `largeIcon.png` exist; `appinfo.json` parses with `id` `com.jagosar.minimal.launcher`, `type` `web`, `main` `index.html`, `icon` `icon.png`; both PNGs start with bytes `89 50 4E 47 0D 0A 1A 0A`; `index.html` contains `<div id="app">` and `src="main.js"`; `main.js` parses with `acorn.parse(code, { ecmaVersion: 2016, sourceType: "script" })`.
  - Must not: contain the identifiers `padStart`, `Object.values`, `Object.entries` or `.includes(` in `main.js` (APIs missing in Chromium 53); emit ES modules or post-2016 syntax (`index.html` contains no `type="module"`; `main.js` contains no `import ` / `export ` statements at top level — guaranteed by `sourceType: "script"` parse succeeding).
  - Check: `launcher/acceptance/build.test.ts`
  - WP: WP-01 (build script), WP-05 (bundle entry): a bundling failure caused by `entry.ts` belongs to WP-05.

### Work packages
**WP-01 · wave 1 · Tooling, packaging and shared types**
- Files: `launcher/package.json`, `launcher/.gitignore` (`node_modules/` and `dist/`), `launcher/package-lock.json`, `launcher/tsconfig.json`, `launcher/tsconfig.test.json`, `launcher/vitest.config.ts`, `launcher/scripts/build.mjs`, `launcher/public/index.html`, `launcher/public/appinfo.json`, `launcher/src/types.ts`, `launcher/src/apps.json`.
- Implements: section 5 types; `buildLauncher`; `appinfo.json`, `index.html`, `apps.json` exactly as in sections 5–6.
- `package.json`: `"name": "minimal-launcher"`, `"version": "0.1.0"`, `"private": true`, `"type": "module"`; scripts `build: node scripts/build.mjs`, `typecheck: tsc -p tsconfig.json && tsc -p tsconfig.test.json`, `test: vitest run --dir src`, `acceptance: vitest run --dir acceptance`, `qa: vitest run --dir qa` (`--dir` makes extra file arguments narrow the run instead of widening it); devDependencies pinned exactly: `typescript 5.9.3`, `esbuild 0.28.2`, `vitest 5.0.5`, `jsdom 30.1.3`, `acorn 8.19.0`, `@types/node 22.20.5`. Run `npm --prefix launcher install` to create the lockfile.
- `vitest.config.ts`: `defineConfig({ test: { environment: "jsdom" } })` from `vitest/config`.
- ACs: AC-009.
- Patterns to mirror: none (no comparable code in the repo, section 4b).
- Gotchas: `build.mjs` runs both from npm (cwd `launcher/`) and imported from tests (cwd = repo root): resolve every path from `fileURLToPath(new URL("..", import.meta.url))`. `style.css` (WP-04) and `src/entry.ts` (WP-05) do not exist yet in wave 1, so the full build — and AC-009 — can only go green after wave 3. `buildLauncher` must reject with a clear message naming any missing input file. Do not create those files yourself; your validation is limited to `typecheck` and `node --check`. A PNG needs signature, `IHDR`, `IDAT` (zlib-deflated rows, each prefixed with filter byte 0) and `IEND`, each chunk with its CRC-32 (use `zlib.crc32`, the same CRC-32 PNG uses; available since Node 22.2).
- Imports/APIs: `import { build } from "esbuild"`; `node:fs/promises` (`mkdir`, `copyFile`, `writeFile`), `node:path`, `node:url`, `node:zlib` (`deflateSync`, `crc32`).
- Validation: `node --version` (must be ≥ 22.12; otherwise report it as a blocker), `npm --prefix launcher install`, `npm --prefix launcher run typecheck` (with only `types.ts` present it must pass), `node --check launcher/scripts/build.mjs`.

**WP-02 · wave 2 · App list and clock**
- Files: `launcher/src/apps.ts`, `launcher/src/apps.test.ts`, `launcher/src/clock.ts`, `launcher/src/clock.test.ts`.
- Implements: `parseAppList`, `AppListError`, `formatClock`. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-001, AC-002, AC-003.
- Patterns to mirror: none.
- Gotchas: check `typeof x === "object" && x !== null && !Array.isArray(x)` for elements; detect duplicates with an object map or `indexOf` (no `Set` iteration tricks needed; `Set` is fine in ES2015). Manual zero padding: `(n < 10 ? "0" : "") + n`.
- Imports: `import type { AppEntry, ClockText } from "./types"`.
- Validation: `npm --prefix launcher test -- src/apps.test.ts src/clock.test.ts`; `npm --prefix launcher run typecheck` with zero errors in your own files (errors in a sibling's unfinished files are not yours).

**WP-03 · wave 2 · Remote navigation and app launching**
- Files: `launcher/src/navigation.ts`, `launcher/src/navigation.test.ts`, `launcher/src/launch.ts`, `launcher/src/launch.test.ts`.
- Implements: `KEY_CODES`, `keyFromCode`, `moveFocus`, `LaunchError`, `LAUNCH_URI`, `defaultBridgeFactory`, `launchApp`. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-004, AC-005.
- Patterns to mirror: none.
- Gotchas: set `onservicecallback` before `call` (a real bridge may answer synchronously). Guard against a second callback (ignore it). For `extends Error` with target ES2015, set `this.name` and keep the prototype chain (ES2015 classes do this correctly; do not downlevel to ES5).
- Imports: `import type { FocusState, NavKey, ServiceBridge } from "./types"`.
- Validation: `npm --prefix launcher test -- src/navigation.test.ts src/launch.test.ts`; `npm --prefix launcher run typecheck` with zero errors in your own files (errors in a sibling's unfinished files are not yours).

**WP-04 · wave 2 · Rendering and style**
- Files: `launcher/src/view.ts`, `launcher/src/view.test.ts`, `launcher/public/style.css`.
- Implements: `renderLauncher` and the stylesheet of section 7, keyed on the `data-role` attributes and `.is-focused`. Consumes `types.ts` by implementation (WP-01).
- ACs: AC-006.
- Patterns to mirror: none.
- Gotchas: clear `root` with `while (root.firstChild) root.removeChild(root.firstChild)` (or `textContent = ""`); create nodes with `root.ownerDocument.createElement` so it works in any document. Chromium 53 supports flexbox but not CSS `gap` on flex containers (that arrived in Chromium 84): use margins between tiles. No CSS variables are needed; if used, they work in 53.
- Imports: `import type { AppEntry, ClockText, FocusState } from "./types"`.
- Validation: `npm --prefix launcher test -- src/view.test.ts`; `npm --prefix launcher run typecheck` with zero errors in your own files (errors in a sibling's unfinished files are not yours).

**WP-05 · wave 3 · Wiring and entry point**
- Files: `launcher/src/main.ts`, `launcher/src/main.test.ts`, `launcher/src/entry.ts`.
- Implements: `CLOCK_TICK_MS`, `LauncherOptions`, `LauncherHandle`, `startLauncher`, `entry.ts`. Consumes by implementation `parseAppList`, `AppListError` (WP-02), `formatClock` (WP-02), `keyFromCode`, `moveFocus` (WP-03), `launchApp` (WP-03), `renderLauncher` (WP-04).
- ACs: AC-007, AC-008, AC-009 (shared with WP-01: the bundle starts at your `entry.ts`).
- Patterns to mirror: none.
- Gotchas: read `event.keyCode` (webOS 4 delivers Back only as keyCode 461; `event.key` is unreliable there). Re-render on every state change by calling `renderLauncher` with the full state. A launch rejection settles asynchronously: re-render inside the `.catch`. `stop()` must use the same function reference given to `addEventListener`.
- Imports: `import { parseAppList, AppListError } from "./apps"`, `import { formatClock } from "./clock"`, `import { keyFromCode, moveFocus } from "./navigation"`, `import { launchApp } from "./launch"`, `import { renderLauncher } from "./view"`, `import apps from "./apps.json"` (entry only).
- Validation: `npm --prefix launcher test`, `npm --prefix launcher run typecheck`, `npm --prefix launcher run build`.

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
    "Bash(node --check launcher/scripts/build.mjs)"
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
     "files": ["launcher/package.json", "launcher/.gitignore", "launcher/package-lock.json", "launcher/tsconfig.json", "launcher/tsconfig.test.json", "launcher/vitest.config.ts",
               "launcher/scripts/build.mjs", "launcher/public/index.html", "launcher/public/appinfo.json",
               "launcher/src/types.ts", "launcher/src/apps.json"],
     "acs": ["AC-009"]},
    {"id": "WP-02", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/apps.ts", "launcher/src/apps.test.ts", "launcher/src/clock.ts", "launcher/src/clock.test.ts"],
     "acs": ["AC-001", "AC-002", "AC-003"]},
    {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/navigation.ts", "launcher/src/navigation.test.ts", "launcher/src/launch.ts", "launcher/src/launch.test.ts"],
     "acs": ["AC-004", "AC-005"]},
    {"id": "WP-04", "wave": 2, "depends_on": ["WP-01"],
     "files": ["launcher/src/view.ts", "launcher/src/view.test.ts", "launcher/public/style.css"],
     "acs": ["AC-006"]},
    {"id": "WP-05", "wave": 3, "depends_on": ["WP-02", "WP-03", "WP-04"],
     "files": ["launcher/src/main.ts", "launcher/src/main.test.ts", "launcher/src/entry.ts"],
     "acs": ["AC-007", "AC-008", "AC-009"]}
  ]
}
```

## 9. Test strategy
- **Unit:** each WP writes `src/<module>.test.ts` next to its code with Vitest; jsdom by default. Doubles (fake bridge, spies) live in the test file itself.
- **Acceptance:** the test-author writes `launcher/acceptance/*.test.ts` in wave 0, one `describe` per AC whose title starts with the AC id (e.g. `describe("AC-005 · Launching through the Luna bus", …)`), so failures are attributed to the owning WP. Before WP-01 exists, `npm --prefix launcher run acceptance` fails (no package.json): that is the expected red.
- **QA:** exploratory, after integration, in `launcher/qa/`: build with `npm --prefix launcher run build`, run its tests with `npm --prefix launcher run qa`; load `launcher/dist/index.html` in jsdom with a fake `PalmServiceBridge`, drive the keys, and review the CSS against section 7 (safe area, focus, no `gap`). Real-TV testing is out of reach (section 11).
- All commands run from the repo root; no network beyond `npm install` from the npm registry.

## 10. Decisions
| Decision | Discarded alternative | Reason |
|---|---|---|
| A regular app in Developer Mode, apps from a bundled JSON (user) | Rooted TV reading installed apps via privileged Luna calls | User has no root; those APIs are undocumented and cannot be tested here. |
| Scope: clock + one row of apps (user) | Favourites, dynamic background, settings | Smallest useful version; the rest can come in later designs. |
| TypeScript + esbuild, no framework (user) | Preact; plain ES5 without build | Smallest bundle and fastest start on the TV, with types and testability. |
| Build a folder ready for `ares-package` (user) | Ship only `dist/` | User installs with the webOS CLI; we cannot run it against the TV. |
| Exact versions checked against the npm registry on 2026-10-26 with `npm view` (architect) | Ranges | Reproducible installs; all six versions exist. |
| TypeScript 5.9.3 instead of the current 7.0.2 (architect) | TypeScript 7 (native port) | 5.9 is the mature `tsc`; 7.x is a new major whose CLI/behaviour I have not verified. |
| `lib: ES2015 + DOM` as the API guard, acorn `ecmaVersion: 2016` as the syntax guard (architect) | Polyfills (core-js) | No polyfills needed if the code avoids post-ES2015 APIs; keeps the bundle tiny and makes violations a type error. |
| Talk to `PalmServiceBridge` directly (architect) | LG's `webOSTV.js` library | It is not published on npm (SDK download); the bridge is the primitive it wraps and only `launch` is needed. Exact behaviour on webOS 4 is a risk (section 11). |
| Launch on OK only; Back is swallowed (architect) | Back exits the app | A launcher should not close itself by accident; the system Home button still works. |
| Waves 1 → 2 (×3 in parallel) → 3 (architect) | Everything in one wave with doubles | WP-02/03/04 need a working toolchain (package.json, vitest) to run their own tests, so WP-01 goes first; WP-05 integrates the three real modules and its tests and build need them, so it goes last. Wave 2 keeps three WPs in parallel. |
| Shared types in `types.ts` owned by WP-01 (architect) | Each module exporting its own types | Lets the three wave-2 WPs compile against the same types without depending on each other. |
| No `mirror` references (architect) | Pointing at framework files | Nothing in the repo is comparable (Python CLI / workflow JS); invented mirrors would mislead. |
| Docs only in `launcher/README.md`, code map disabled (architect) | Default docs zone (`README.md`, `docs/`) | Those belong to the framework; the launcher must not touch them. |
| Tiles show the title's initial when there is no icon; icons generated as solid PNGs (architect) | Shipping binary icon files | Implementers cannot author binary images; real artwork can be added later. |
| Spanish UI text, hand-written day/month names (architect) | `Intl.DateTimeFormat("es")` | `Intl` locale data on the TV is not guaranteed; deterministic tests. |

## 11. Risks and open questions
- **Default app ids are unverified** (`netflix`, `youtube.leanback.v4`, `amazon`, `com.webos.app.livetv`, `com.webos.app.browser`). Wrong ids only make that tile fail with "No se pudo abrir …"; the user should check them with `ares-launch --device <tv> --list` or `luna-send` and edit `launcher/src/apps.json`. Does not block any WP.
- **`PalmServiceBridge` behaviour on webOS 4** (constructor available to Developer Mode web apps, callback format) is based on how LG's `webOSTV.js` works, not tested on a device here. If it differs, only `launch.ts` changes.
- **Chromium 53 compatibility** is enforced by syntax (acorn) and API (`lib`) checks, not by a real browser; CSS features are covered by QA review only.
- **Developer Mode** apps on LG TVs stop working when the dev-mode session expires unless it is renewed; outside the scope of this design.
- **OLED burn-in:** a static clock on a black screen can mark the panel over long periods; mitigation (subtle movement, dimming) is a candidate for a later change.
- No real-device test: acceptance and QA run in Node/jsdom only.
- **`npm install` failure** (registry unreachable) blocks WP-01 and therefore everything; WP-01 reports it instead of changing versions. Versions were verified to exist on 2026-10-26.
- Wave-2 WPs run `typecheck` but only own the errors in their files; a `types` failure in verification goes back to the WP that owns the failing file.
- **Node version:** Vitest 5 needs Node ≥ 22.12; WP-01 checks it first.
- The framework's own tests are not in `verify`: only `launcher/` changes, so a regression there is not expected.
