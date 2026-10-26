import type { Tile, ViewModel, WeatherView } from "./types";
import { WEATHER_ICON_SVG } from "./weather-icons";

export const ROW_VIEWPORT_PX = 1696;

const EMPTY_MESSAGE = "No hay apps configuradas";
const BACKGROUND_PATH = "assets/backgrounds/";

export interface LauncherView {
  update(model: ViewModel): void;
  rowOffset(): number;
}

// Adapted from Lightkeeper (MIT, © 2026 Francesco Gola), https://github.com/chiotas/lightkeeper
export function computeRowOffset(itemLeft: number, itemWidth: number, viewport: number, current: number): number {
  if (itemLeft + itemWidth > current + viewport) return itemLeft + itemWidth - viewport;
  if (itemLeft < current) return itemLeft;
  return current;
}

function createWithRole(doc: Document, tag: string, role: string, text?: string): HTMLElement {
  const element = doc.createElement(tag);
  element.setAttribute("data-role", role);
  if (text !== undefined) element.textContent = text;
  return element;
}

function createTile(doc: Document, tile: Tile, rowStyle: string): HTMLElement {
  const element = createWithRole(doc, "div", "app-tile");
  element.className = "tile";
  element.setAttribute("data-tile-id", tile.id);
  element.setAttribute("data-tile-type", tile.type);

  if (rowStyle === "tiles") {
    if (tile.type === "app" && tile.icon) {
      const icon = createWithRole(doc, "img", "app-icon");
      icon.setAttribute("src", tile.icon);
      icon.setAttribute("alt", "");
      element.appendChild(icon);
    } else {
      element.appendChild(createWithRole(doc, "div", "app-initial", tile.title.charAt(0).toUpperCase()));
    }
  }
  element.appendChild(createWithRole(doc, "div", "app-title", tile.title));
  return element;
}

function setFocus(element: Element, isFocused: boolean): void {
  const hasFocus = element.className.indexOf("is-focused") !== -1;
  if (hasFocus === isFocused) return;
  element.className = isFocused ? "tile is-focused" : "tile";
  if (isFocused) element.setAttribute("aria-current", "true");
  else element.removeAttribute("aria-current");
}

function weatherKey(weather: WeatherView): string {
  return weather.now
    ? weather.place + "|" + weather.now.temperature + "|" + weather.now.icon
    : weather.place + "||";
}

export function createLauncherView(root: HTMLElement): LauncherView {
  const doc = root.ownerDocument;
  while (root.firstChild) root.removeChild(root.firstChild);

  const background = createWithRole(doc, "div", "background");
  const veil = createWithRole(doc, "div", "veil");
  const header = doc.createElement("header");
  const clockLine = doc.createElement("div");
  clockLine.className = "clock-line";
  const clockTime = createWithRole(doc, "span", "clock-time");
  const clockMeridiem = createWithRole(doc, "span", "clock-meridiem");
  clockLine.appendChild(clockTime);
  clockLine.appendChild(clockMeridiem);
  const clockDate = createWithRole(doc, "div", "clock-date");
  header.appendChild(clockLine);
  header.appendChild(clockDate);
  const content = doc.createElement("main");

  root.appendChild(background);
  root.appendChild(veil);
  root.appendChild(header);
  root.appendChild(content);

  let weatherElement: HTMLElement | null = null;
  let renderedWeatherKey = "";
  let row: HTMLElement | null = null;
  let emptyElement: HTMLElement | null = null;
  let errorElement: HTMLElement | null = null;
  let renderedTiles: readonly Tile[] | null = null;
  let renderedStyle = "";
  let offset = 0;

  function setText(element: HTMLElement, text: string): void {
    if (element.textContent !== text) element.textContent = text;
  }

  function updateWeather(weather: WeatherView | null): void {
    if (weather === null) {
      if (weatherElement) header.removeChild(weatherElement);
      weatherElement = null;
      renderedWeatherKey = "";
      return;
    }
    if (!weatherElement) {
      weatherElement = createWithRole(doc, "div", "weather");
      header.appendChild(weatherElement);
      renderedWeatherKey = "";
    }
    const key = weatherKey(weather);
    if (key === renderedWeatherKey) return;
    renderedWeatherKey = key;
    const now = weather.now;
    weatherElement.textContent = now ? weather.place + " · " + now.temperature + "°" : weather.place;
    if (!now) return;
    const icon = createWithRole(doc, "span", "weather-icon");
    icon.setAttribute("data-icon", now.icon);
    icon.innerHTML = WEATHER_ICON_SVG[now.icon]; // trusted constants only
    weatherElement.appendChild(icon);
  }

  function rebuildRow(model: ViewModel): void {
    if (row) content.removeChild(row);
    row = null;
    if (emptyElement) content.removeChild(emptyElement);
    emptyElement = null;
    renderedTiles = model.tiles;
    renderedStyle = model.rowStyle;
    offset = 0;

    if (model.tiles.length === 0) {
      emptyElement = createWithRole(doc, "div", "empty", EMPTY_MESSAGE);
      content.appendChild(emptyElement);
      return;
    }
    const newRow = createWithRole(doc, "div", "app-row");
    newRow.setAttribute("data-style", model.rowStyle);
    model.tiles.forEach((tile) => newRow.appendChild(createTile(doc, tile, model.rowStyle)));
    content.appendChild(newRow);
    row = newRow;
  }

  function slideRow(focusIndex: number): void {
    if (!row) return;
    const focused = row.children[focusIndex] as HTMLElement | undefined;
    if (focused) {
      offset = computeRowOffset(focused.offsetLeft, focused.offsetWidth, ROW_VIEWPORT_PX, offset);
    }
    const value = "translateX(-" + offset + "px)";
    row.style.transform = value;
    row.style.webkitTransform = value;
  }

  function updateError(error: string | null): void {
    if (error === null) {
      if (errorElement) root.removeChild(errorElement);
      errorElement = null;
      return;
    }
    if (!errorElement) {
      errorElement = createWithRole(doc, "div", "error");
      root.appendChild(errorElement);
    }
    setText(errorElement, error);
  }

  return {
    update(model: ViewModel): void {
      background.style.backgroundImage = "url(" + BACKGROUND_PATH + model.background + ".jpg)";
      veil.style.opacity = String(model.veil / 100);
      setText(clockTime, model.clock.time);
      setText(clockMeridiem, model.clock.meridiem);
      setText(clockDate, model.clock.date);
      updateWeather(model.weather);

      if (model.tiles !== renderedTiles || model.rowStyle !== renderedStyle) rebuildRow(model);
      if (row) {
        for (let i = 0; i < row.children.length; i++) setFocus(row.children[i], i === model.focusIndex);
      }
      slideRow(model.focusIndex);
      updateError(model.error);
    },
    rowOffset(): number {
      return offset;
    },
  };
}
