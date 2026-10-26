import { describe, it, expect, vi, afterEach } from "vitest";
import {
  WEATHER_TIMEOUT_MS,
  WeatherError,
  fetchWeather,
  weatherIcon,
  weatherUrl,
  xhrGet,
} from "./weather";

const PLACE = { name: "Madrid", lat: 40.4168, lon: -3.7038 };
const BODY = '{"current":{"temperature_2m":17.6,"weather_code":2,"is_day":1}}';

class FakeXhr {
  static last: FakeXhr;
  method = "";
  url = "";
  timeout = 0;
  status = 0;
  responseText = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }
  send(): void {}
}

const globalWithXhr = globalThis as unknown as { XMLHttpRequest: unknown };
const realXhr = globalWithXhr.XMLHttpRequest;

afterEach(() => {
  globalWithXhr.XMLHttpRequest = realXhr;
});

describe("weatherUrl", () => {
  it("builds the celsius url", () => {
    expect(weatherUrl(PLACE, "c")).toBe(
      "https://api.open-meteo.com/v1/forecast?latitude=40.4168&longitude=-3.7038&current=temperature_2m,weather_code,is_day&timezone=auto"
    );
  });

  it("adds the fahrenheit unit", () => {
    expect(weatherUrl(PLACE, "f").slice(-"&temperature_unit=fahrenheit".length)).toBe("&temperature_unit=fahrenheit");
  });
});

describe("weatherIcon", () => {
  it("maps codes to icons", () => {
    expect(weatherIcon(0, true)).toBe("sun");
    expect(weatherIcon(1, false)).toBe("moon");
    expect(weatherIcon(3, false)).toBe("cloud");
    expect(weatherIcon(48, true)).toBe("fog");
    expect(weatherIcon(61, true)).toBe("rain");
    expect(weatherIcon(77, true)).toBe("snow");
    expect(weatherIcon(85, true)).toBe("snow");
    expect(weatherIcon(82, true)).toBe("rain");
    expect(weatherIcon(99, true)).toBe("storm");
  });
});

describe("fetchWeather", () => {
  it("resolves the rounded temperature and icon with one call", async () => {
    const stub = vi.fn((_url: string, _t: number) => Promise.resolve(BODY));
    await expect(fetchWeather(PLACE, "c", stub)).resolves.toEqual({ temperature: 18, icon: "cloud" });
    expect(stub).toHaveBeenCalledTimes(1);
    expect(stub.mock.calls[0][1]).toBe(WEATHER_TIMEOUT_MS);
  });

  it("rejects invalid JSON and wrong shapes", async () => {
    const bodies = ["oops", "null", '{"current":{"temperature_2m":1,"weather_code":2,"is_day":2}}'];
    for (const body of bodies) {
      const error = await fetchWeather(PLACE, "c", () => Promise.resolve(body)).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(WeatherError);
      expect((error as Error).message).toBe("Respuesta inválida");
    }
  });

  it("wraps foreign rejections and keeps WeatherError", async () => {
    const wrapped = await fetchWeather(PLACE, "c", () => Promise.reject(new Error("x"))).catch((e: unknown) => e);
    expect(wrapped).toBeInstanceOf(WeatherError);
    expect((wrapped as Error).message).toBe("x");
    const original = new WeatherError("Sin conexión");
    await expect(fetchWeather(PLACE, "c", () => Promise.reject(original))).rejects.toBe(original);
  });
});

describe("xhrGet", () => {
  function start(): Promise<string> {
    globalWithXhr.XMLHttpRequest = FakeXhr;
    return xhrGet("https://x.test", 1234);
  }

  it("resolves the response text on 200 and sets the timeout", async () => {
    const promise = start();
    expect(FakeXhr.last.timeout).toBe(1234);
    expect(FakeXhr.last.method).toBe("GET");
    FakeXhr.last.status = 200;
    FakeXhr.last.responseText = "body";
    FakeXhr.last.onload?.();
    await expect(promise).resolves.toBe("body");
  });

  it("rejects with the HTTP status", async () => {
    const promise = start();
    FakeXhr.last.status = 500;
    FakeXhr.last.onload?.();
    await expect(promise).rejects.toThrow("HTTP 500");
  });

  it("rejects on timeout and on network error", async () => {
    const timedOut = start();
    FakeXhr.last.ontimeout?.();
    await expect(timedOut).rejects.toThrow("Tiempo agotado");
    const failed = start();
    FakeXhr.last.onerror?.();
    await expect(failed).rejects.toThrow("Sin conexión");
  });
});
