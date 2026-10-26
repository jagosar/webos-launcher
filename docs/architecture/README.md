# Architecture decisions

| # | Date | Title | Type | Key decisions |
|---|---|---|---|---|
| [0001](0001-webos-minimal-launcher.md) | 2026-10-26 | Minimal webOS 4 launcher | feature | Regular Developer Mode app (no root) with the app list in a bundled JSON; TypeScript 5.9 + esbuild targeting Chromium 53 with `lib` ES2015 and `types: []` as the API guard; 5 WPs in 3 waves (tooling, then 3 in parallel, then wiring). |
| [0002](0002-lightkeeper-style-launcher.md) | 2026-10-26 | Lightkeeper-style launcher (v2) | change | Ideas from chiotas/lightkeeper that work without root: sliding row, "names" style, picture + veil, Open-Meteo weather, one bundled `config.json`; Back calls `platformBack()` and `localStorage` is allowed (both change 0001); free assets (Jost OFL, public-domain paintings) bundled, never the all-rights-reserved photo. |
