import { parseConfig, ConfigError, DEFAULT_CONFIG } from "./config";
import { formatClockWithMode } from "./clock";
import { keyFromCode, moveFocus } from "./navigation";
import { launchApp, launchTile, LG_HOME_ID } from "./launch";
import { getPlatform, getStorage, loadLastIndex, saveLastIndex } from "./system";
import { fetchWeather } from "./weather";
import { createLauncherView } from "./view";
import type {
  FocusState,
  KeyValueStore,
  LauncherConfig,
  PlatformApi,
  Tile,
  Units,
  ViewModel,
  WeatherNow,
  WeatherPlace,
} from "./types";

export const CLOCK_TICK_MS = 1000;
export const WEATHER_REFRESH_MS = 1800000;
export const INVALID_CONFIG_ERROR = "Configuración inválida";
const HOME_ERROR = "No se pudo abrir el Home de LG";

export interface LauncherOptions {
  root: HTMLElement;
  config: unknown; // raw JSON, validated with parseConfig
  now?: () => Date;
  launchTile?: (tile: Tile) => Promise<void>;
  launchApp?: (appId: string) => Promise<void>; // used for LG Home
  platform?: PlatformApi | null;
  storage?: KeyValueStore | null;
  fetchWeather?: (place: WeatherPlace, units: Units) => Promise<WeatherNow>;
  target?: EventTarget;
}

export interface LauncherHandle {
  stop(): void;
}

export function startLauncher(options: LauncherOptions): LauncherHandle {
  const { root } = options;
  const now = options.now ?? (() => new Date());
  const openTile = options.launchTile ?? ((tile: Tile) => launchTile(tile));
  const openApp = options.launchApp ?? ((appId: string) => launchApp(appId));
  const platform = options.platform !== undefined ? options.platform : getPlatform();
  const storage = options.storage !== undefined ? options.storage : getStorage();
  const loadWeather = options.fetchWeather ?? ((place: WeatherPlace, units: Units) => fetchWeather(place, units));
  const target: EventTarget = options.target ?? root.ownerDocument;

  let config: LauncherConfig = DEFAULT_CONFIG;
  let error: string | null = null;
  let isConfigInvalid = false;
  try {
    config = parseConfig(options.config);
  } catch (e) {
    if (!(e instanceof ConfigError)) throw e;
    isConfigInvalid = true;
    error = INVALID_CONFIG_ERROR;
  }

  const tiles = config.tiles;
  let focus: FocusState = { index: loadLastIndex(storage, tiles.length), count: tiles.length };
  let weatherNow: WeatherNow | null = null;
  let isLaunching = false;
  let isStopped = false;
  const view = createLauncherView(root);

  const render = (): void => {
    const model: ViewModel = {
      tiles,
      focusIndex: focus.index,
      rowStyle: config.rowStyle,
      clock: formatClockWithMode(now(), config.clock),
      weather: config.weather ? { place: config.weather.name, now: weatherNow } : null,
      background: config.background,
      veil: config.veil,
      error,
    };
    view.update(model);
  };

  const clearLaunchError = (): void => {
    if (!isConfigInvalid) error = null;
  };

  const runGuarded = (start: () => Promise<void>, failure: string): void => {
    isLaunching = true;
    start().then(
      () => {
        isLaunching = false;
      },
      () => {
        isLaunching = false;
        if (isStopped || isConfigInvalid) return;
        error = failure;
        render();
      }
    );
  };

  const openFocusedTile = (): void => {
    if (tiles.length === 0 || isLaunching) return;
    const tile = tiles[focus.index];
    clearLaunchError();
    saveLastIndex(storage, focus.index);
    runGuarded(() => openTile(tile), "No se pudo abrir " + tile.title);
  };

  const openHome = (): void => {
    if (isLaunching) return;
    runGuarded(() => openApp(LG_HOME_ID), HOME_ERROR);
  };

  const onKeyDown = (event: Event): void => {
    const key = keyFromCode((event as KeyboardEvent).keyCode);
    if (key === null) return;
    event.preventDefault();
    if (key === "up") return;
    if (key === "back") {
      if (platform !== null) platform.platformBack();
      return;
    }
    if (key === "ok") {
      openFocusedTile();
      return;
    }
    if (key === "down") {
      openHome();
      return;
    }
    focus = moveFocus(focus, key);
    clearLaunchError();
    render();
  };

  const refreshWeather = (place: WeatherPlace): void => {
    loadWeather(place, config.units).then(
      (result) => {
        if (isStopped) return;
        weatherNow = result;
        render();
      },
      () => {
        // Weather is decoration: keep the last value and show no error.
      }
    );
  };

  render();
  const clockId = setInterval(render, CLOCK_TICK_MS);
  let weatherId: ReturnType<typeof setInterval> | null = null;
  const place = config.weather;
  if (place !== null) {
    refreshWeather(place);
    weatherId = setInterval(() => refreshWeather(place), WEATHER_REFRESH_MS);
  }
  target.addEventListener("keydown", onKeyDown);

  return {
    stop(): void {
      isStopped = true;
      clearInterval(clockId);
      if (weatherId !== null) clearInterval(weatherId);
      target.removeEventListener("keydown", onKeyDown);
    },
  };
}
