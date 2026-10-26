# Lightkeeper-style webOS 4 launcher

A calm home screen for LG OLED TVs running webOS 4. It shows a picture under a dark veil, the time, the date in Spanish, the weather for one city, and a single row of apps, HDMI inputs and web pages. The row slides so the focused item is always on screen.

It is a regular web app installed in Developer Mode, without root. It does not replace the system home: you open it like any other app, and it launches the others.

Everything is configured in one file, [`launcher/src/config.json`](launcher/src/config.json). Build, type-check and test commands, the full configuration reference and the known limits are in [`launcher/README.md`](launcher/README.md).

## Quick start

```sh
npm --prefix launcher install
npm --prefix launcher run build        # writes launcher/dist/
```

Then package and install with the webOS CLI (not run by this project). `ares-package` creates the `.ipk` from `launcher/dist/`; `ares-install` installs it on the TV:

```sh
ares-package launcher/dist
ares-install --device <your-tv> <the .ipk that ares-package created>
```

Requirements: Node 22.12 or later, and a TV in Developer Mode.

## Remote

| Key | Action |
|---|---|
| ← → | Move (the row slides) |
| OK | Open the focused tile |
| Back | Leave the launcher |
| ↓ | Open LG's Home |

## Status

Version 2.0.0. Tested with unit, acceptance and QA tests in Node/jsdom. **Not yet tested on a real TV**: the behaviour of `PalmServiceBridge`, Back, the Home key and the app ids in the default configuration are unverified.

## Licence and credits

Code and assets are credited in [`launcher/public/assets/CREDITS.md`](launcher/public/assets/CREDITS.md). The launcher's ideas come from [Lightkeeper](https://github.com/chiotas/lightkeeper) (MIT).

---

## About this repository

This repository also contains [agentic-flow](https://github.com/jagosar/agentic-flow), the framework used to design and build the launcher (`.claude/`, `plugin/`, `docs/`). The launcher's design records are in `docs/architecture/`.
