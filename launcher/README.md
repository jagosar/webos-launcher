# Lightkeeper-style webOS 4 launcher

A calm launcher app for an LG OLED TV running webOS 4, inspired by [lightkeeper](https://github.com/chiotas/lightkeeper) but working without root. It shows a background picture under a dark veil, the time (24 or 12 h), the date (in Spanish), the current weather for one city, and a single row of apps, HDMI inputs and web pages. The row slides horizontally so the focused item is always on screen. Navigate with the remote's d-pad and open the focused item with OK.

It is a regular web app installed in Developer Mode (no root). It does not replace the system home: you open it like any other app, and it launches other apps through `luna://com.webos.applicationManager/launch` using `PalmServiceBridge`.

Everything is configured in one bundled file, `src/config.json`, because a non-rooted app cannot reliably list the installed apps.

## Requirements

- Node 22.12 or later, and npm.
- To install on the TV: the webOS CLI (`ares-package`, `ares-install`) and a TV in Developer Mode. These are not run by this project.

## Commands

Run them from the repository root:

| Purpose | Command |
|---|---|
| Install dependencies | `npm --prefix launcher install` |
| Build to `launcher/dist/` | `npm --prefix launcher run build` |
| Type-check (sources and tests) | `npm --prefix launcher run typecheck` |
| Unit tests | `npm --prefix launcher test` |
| Acceptance tests | `npm --prefix launcher run acceptance` |
| QA tests | `npm --prefix launcher run qa` |

## Build output

`npm --prefix launcher run build` writes `launcher/dist/` with `index.html`, `main.js`, `style.css`, `appinfo.json`, `assets/` (the Jost font and the backgrounds), `icon.png` and `largeIcon.png`. `main.js` is a single script bundled by esbuild for Chromium 53 (the webOS 4 web engine). The icons are generated solid-colour PNGs. The folder is ready to be packaged with `ares-package`.

## Configuration

Edit `src/config.json` and rebuild. Example (the shipped default):

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
    { "type": "web", "title": "El País", "url": "https://elpais.com" }
  ]
}
```

| Field | Values | Default |
|---|---|---|
| `rowStyle` | `names` (text, the selected one brighter with a line under it) or `tiles` (square tiles) | `names` |
| `clock` | `24`, or `12` (shows `7:05` and `AM`/`PM`) | `24` |
| `background` | `night`, `dawn`, `southampton`, `penobscot`, `northcape`, `monk`, `entrance`, `chinasea` | `night` |
| `veil` | integer 0 to 100; the veil opacity is `veil / 100` | `80` |
| `weather` | `{ "name", "lat", "lon" }`, or `null` to hide the weather | `null` |
| `units` | `c` or `f` | `c` |
| `tiles` | array in display order (required) | none |

Tiles:

- `type` (optional): `app` (default), `input` or `web`. Inputs (for example `com.webos.app.hdmi1`) launch like apps.
- `id` (required for `app` and `input`): the webOS app id.
- `title` (required): the visible name.
- `icon` (optional, `app` only): a URL or path relative to `index.html`.
- `url` (required for `web`): must start with `http://` or `https://`. The tile opens the TV browser (`com.webos.app.browser`) with that address.
- Ids must be unique.

If the file is invalid, the launcher falls back to the defaults (no tiles) and shows `Configuración inválida`. A wrong app id only makes that tile show `No se pudo abrir <title>`; the ids in the default file have not been verified on a TV.

## Weather

When `weather` is set, the launcher asks Open-Meteo (`https://api.open-meteo.com`, no API key) for the current weather every 30 minutes. If the request fails, only the city name is shown.

## Remote keys

| Key | Action |
|---|---|
| Left / Right | Move focus (no wrap-around); the row slides to keep it on screen |
| OK | Open the focused tile; its index is remembered and focused on the next start |
| Back | Leaves the launcher (`webOSSystem.platformBack()`) |
| Down | Opens LG's Home (`com.webos.app.home`) |
| Up | Does nothing |

Escape behaves like Back, for desktop testing.

## Layout

- `src/`: TypeScript sources, with unit tests next to them (`*.test.ts`).
- `public/`: static files copied to the build (`index.html`, `style.css`, `appinfo.json`, `assets/`). Asset sources and licences are in `public/assets/CREDITS.md`.
- `scripts/build.mjs`: the build script.
- `acceptance/` and `qa/`: acceptance and QA tests.

## Known limits

- Not tested on a real TV; behaviour of `PalmServiceBridge` on webOS 4 is based on how LG's tooling uses it.
- Apps installed in Developer Mode stop working when the dev-mode session expires unless renewed.
- A static clock on an OLED panel can cause burn-in over long periods.
