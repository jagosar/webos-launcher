import type { Units, WeatherIcon, WeatherNow, WeatherPlace } from "./types";

// Adapted from Lightkeeper (MIT, © 2026 Francesco Gola), https://github.com/chiotas/lightkeeper
export { WEATHER_ICON_SVG } from "./weather-icons";

export class WeatherError extends Error {
  name = "WeatherError";
}

export const WEATHER_TIMEOUT_MS = 8000;

const FORECAST_URL = "https://api.open-meteo.com/v1/forecast";
const HTTP_OK = 200;
const MAX_CLEAR_CODE = 1;
const MAX_CLOUD_CODE = 3;
const MAX_FOG_CODE = 48;
const MIN_STORM_CODE = 95;
const MIN_SNOW_CODE = 71;
const MAX_SNOW_CODE = 77;
const SNOW_SHOWER_CODES = [85, 86];
const INVALID_RESPONSE_TEXT = "Respuesta inválida";

export type HttpGet = (url: string, timeoutMs: number) => Promise<string>;

export function weatherUrl(place: WeatherPlace, units: Units): string {
  const base =
    FORECAST_URL +
    "?latitude=" + String(place.lat) +
    "&longitude=" + String(place.lon) +
    "&current=temperature_2m,weather_code,is_day&timezone=auto";
  return units === "f" ? base + "&temperature_unit=fahrenheit" : base;
}

export function weatherIcon(code: number, isDay: boolean): WeatherIcon {
  if (code <= MAX_CLEAR_CODE) {
    return isDay ? "sun" : "moon";
  }
  if (code <= MAX_CLOUD_CODE) {
    return "cloud";
  }
  if (code <= MAX_FOG_CODE) {
    return "fog";
  }
  if (code >= MIN_STORM_CODE) {
    return "storm";
  }
  if ((code >= MIN_SNOW_CODE && code <= MAX_SNOW_CODE) || SNOW_SHOWER_CODES.indexOf(code) !== -1) {
    return "snow";
  }
  return "rain";
}

export function xhrGet(url: string, timeoutMs: number): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("GET", url);
    request.timeout = timeoutMs;
    request.onload = (): void => {
      if (request.status === HTTP_OK) {
        resolve(request.responseText);
        return;
      }
      reject(new WeatherError("HTTP " + request.status));
    };
    request.ontimeout = (): void => reject(new WeatherError("Tiempo agotado"));
    request.onerror = (): void => reject(new WeatherError("Sin conexión"));
    request.send();
  });
}

function isPlainObject(value: unknown): value is { [key: string]: unknown } {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseWeather(body: string): WeatherNow {
  let data: unknown;
  try {
    data = JSON.parse(body);
  } catch (error) {
    throw new WeatherError(INVALID_RESPONSE_TEXT);
  }
  const current = isPlainObject(data) ? data.current : null;
  if (!isPlainObject(current)) {
    throw new WeatherError(INVALID_RESPONSE_TEXT);
  }
  const { temperature_2m: temperature, weather_code: code, is_day: isDay } = current;
  if (
    typeof temperature !== "number" || !isFinite(temperature) ||
    typeof code !== "number" || !isFinite(code) ||
    (isDay !== 0 && isDay !== 1)
  ) {
    throw new WeatherError(INVALID_RESPONSE_TEXT);
  }
  return { temperature: Math.round(temperature), icon: weatherIcon(code, isDay === 1) };
}

export function fetchWeather(place: WeatherPlace, units: Units, httpGet?: HttpGet): Promise<WeatherNow> {
  return (httpGet || xhrGet)(weatherUrl(place, units), WEATHER_TIMEOUT_MS).then(
    parseWeather,
    (error: unknown): never => {
      if (error instanceof WeatherError) {
        throw error;
      }
      throw new WeatherError(error instanceof Error ? error.message : "Error desconocido");
    }
  );
}
