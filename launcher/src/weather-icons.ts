// Adapted from Lightkeeper (MIT, © 2026 Francesco Gola), https://github.com/chiotas/lightkeeper
import type { WeatherIcon } from "./types";

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">';
const SVG_CLOSE = "</svg>";

const HIGH_CLOUD =
  '<path d="M6.5 15h11a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.3 8.1 3.5 3.5 0 0 0 6.5 15z"/>';

function svg(body: string): string {
  return SVG_OPEN + body + SVG_CLOSE;
}

/** Trusted constants: the only strings the view may put in innerHTML. */
export const WEATHER_ICON_SVG: { readonly [icon in WeatherIcon]: string } = {
  sun: svg(
    '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>',
  ),
  moon: svg('<path d="M20 14.6A8.2 8.2 0 0 1 9.4 4 8.2 8.2 0 1 0 20 14.6z"/>'),
  cloud: svg(
    '<path d="M6.5 18h11a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.3 11.1 3.5 3.5 0 0 0 6.5 18z"/>',
  ),
  fog: svg('<path d="M4 9h16M6 13h12M4 17h16"/>'),
  rain: svg(HIGH_CLOUD + '<path d="M8.5 18l-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5"/>'),
  snow: svg(HIGH_CLOUD + '<path d="M8 19.5h.01M12 19.5h.01M16 19.5h.01" stroke-width="2.4"/>'),
  storm: svg(HIGH_CLOUD + '<path d="M12.5 16l-2 3h3l-2 3"/>'),
};
