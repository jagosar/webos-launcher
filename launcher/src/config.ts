import { BACKGROUND_IDS } from "./types";
import type {
  BackgroundId,
  ClockMode,
  LauncherConfig,
  RowStyle,
  Tile,
  Units,
  WeatherPlace,
} from "./types";

export class ConfigError extends Error {
  name = "ConfigError";
}

export const DEFAULT_CONFIG: LauncherConfig = {
  tiles: [],
  rowStyle: "names",
  clock: "24",
  background: "night",
  veil: 80,
  weather: null,
  units: "c",
};

const MIN_VEIL = 0;
const MAX_VEIL = 100;
const MAX_LAT = 90;
const MAX_LON = 180;
const ROW_STYLES: readonly string[] = ["names", "tiles"];
const CLOCK_MODES: readonly string[] = ["24", "12"];
const UNITS: readonly string[] = ["c", "f"];
const TILE_TYPES: readonly string[] = ["app", "input", "web"];

type PlainObject = { [key: string]: unknown };

function isPlainObject(value: unknown): value is PlainObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidTile(index: number, reason: string): ConfigError {
  return new ConfigError(`Tile inválido en la posición ${index}: ${reason}`);
}

function requireText(value: unknown, field: string, index: number): string {
  const text = typeof value === "string" ? value.trim() : "";
  if (text === "") {
    throw invalidTile(index, `falta ${field}`);
  }
  return text;
}

function isHttpUrl(url: string): boolean {
  const lower = url.toLowerCase();
  return lower.indexOf("http://") === 0 || lower.indexOf("https://") === 0;
}

function parseWebTile(item: PlainObject, index: number): Tile {
  const title = requireText(item.title, "title", index);
  const url = requireText(item.url, "url", index);
  if (!isHttpUrl(url)) {
    throw invalidTile(index, "url debe empezar por http:// o https://");
  }
  return { type: "web", id: "web:" + url, title, url };
}

function parseWebOsTile(item: PlainObject, type: "app" | "input", index: number): Tile {
  const id = requireText(item.id, "id", index);
  const title = requireText(item.title, "title", index);
  if (!("icon" in item) || item.icon === undefined) {
    return { type, id, title };
  }
  if (type !== "app") {
    throw invalidTile(index, "icon solo se permite en apps");
  }
  if (typeof item.icon !== "string" || item.icon === "") {
    throw invalidTile(index, "icon debe ser un texto no vacío");
  }
  return { type, id, title, icon: item.icon };
}

function parseTile(item: unknown, index: number): Tile {
  if (!isPlainObject(item)) {
    throw invalidTile(index, "debe ser un objeto");
  }
  const type = item.type === undefined ? "app" : item.type;
  if (typeof type !== "string" || TILE_TYPES.indexOf(type) === -1) {
    throw invalidTile(index, "type desconocido");
  }
  if (type === "web") {
    return parseWebTile(item, index);
  }
  return parseWebOsTile(item, type as "app" | "input", index);
}

function parseTiles(raw: unknown): Tile[] {
  if (!Array.isArray(raw)) {
    throw new ConfigError("tiles debe ser un array");
  }
  const seenIds: string[] = [];
  return raw.map((item: unknown, index: number): Tile => {
    const tile = parseTile(item, index);
    if (seenIds.indexOf(tile.id) !== -1) {
      throw new ConfigError(`Tile duplicado: ${tile.id}`);
    }
    seenIds.push(tile.id);
    return tile;
  });
}

function parseChoice(value: unknown, allowed: readonly string[], field: string): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== "string" || allowed.indexOf(value) === -1) {
    throw new ConfigError(`${field} debe ser uno de: ${allowed.join(", ")}`);
  }
  return value;
}

function parseVeil(value: unknown): number {
  if (value === undefined) {
    return DEFAULT_CONFIG.veil;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < MIN_VEIL || value > MAX_VEIL) {
    throw new ConfigError(`veil debe ser un entero entre ${MIN_VEIL} y ${MAX_VEIL}`);
  }
  return value;
}

function isCoordinate(value: unknown, limit: number): value is number {
  return typeof value === "number" && isFinite(value) && value >= -limit && value <= limit;
}

function parseWeather(value: unknown): WeatherPlace | null {
  if (value === undefined || value === null) {
    return null;
  }
  const name = isPlainObject(value) && typeof value.name === "string" ? value.name.trim() : "";
  if (!isPlainObject(value) || name === "" || !isCoordinate(value.lat, MAX_LAT) || !isCoordinate(value.lon, MAX_LON)) {
    throw new ConfigError("weather debe ser null o { name, lat, lon } válidos");
  }
  return { name, lat: value.lat, lon: value.lon };
}

export function parseConfig(raw: unknown): LauncherConfig {
  if (!isPlainObject(raw)) {
    throw new ConfigError("La configuración debe ser un objeto");
  }
  const tiles = parseTiles(raw.tiles);
  return {
    tiles,
    rowStyle: (parseChoice(raw.rowStyle, ROW_STYLES, "rowStyle") as RowStyle | undefined) ?? DEFAULT_CONFIG.rowStyle,
    clock: (parseChoice(raw.clock, CLOCK_MODES, "clock") as ClockMode | undefined) ?? DEFAULT_CONFIG.clock,
    background:
      (parseChoice(raw.background, BACKGROUND_IDS as readonly string[], "background") as BackgroundId | undefined) ??
      DEFAULT_CONFIG.background,
    veil: parseVeil(raw.veil),
    weather: parseWeather(raw.weather),
    units: (parseChoice(raw.units, UNITS, "units") as Units | undefined) ?? DEFAULT_CONFIG.units,
  };
}
