import { describe, it, expect, vi } from "vitest";
import { fetchWeather, weatherIcon, WeatherError, WEATHER_ICON_SVG } from "../src/weather";

const PLACE = { name: "Madrid", lat: 40.4168, lon: -3.7038 };
const BODY = '{"current":{"temperature_2m":17.6,"weather_code":2,"is_day":1}}';
const BASE =
  "https://api.open-meteo.com/v1/forecast?latitude=40.4168&longitude=-3.7038&current=temperature_2m,weather_code,is_day&timezone=auto";

describe("AC-015 · Weather from Open-Meteo", () => {
  it("test_ac_015_celsius_url_and_result", async () => {
    const stub = vi.fn((_url: string, _t: number) => Promise.resolve(BODY));
    const now = await fetchWeather(PLACE, "c", stub);
    expect(stub).toHaveBeenCalledTimes(1);
    expect(stub.mock.calls[0][0]).toBe(BASE);
    expect(now).toEqual({ temperature: 18, icon: "cloud" });
  });

  it("test_ac_015_fahrenheit_url", async () => {
    const stub = vi.fn((_url: string, _t: number) => Promise.resolve(BODY));
    await fetchWeather(PLACE, "f", stub);
    expect(stub).toHaveBeenCalledTimes(1);
    expect(stub.mock.calls[0][0]).toBe(BASE + "&temperature_unit=fahrenheit");
  });

  it("test_ac_015_invalid_body_is_weather_error", async () => {
    const stub = vi.fn(() => Promise.resolve("oops"));
    const error = await fetchWeather(PLACE, "c", stub).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(WeatherError);
    expect((error as Error).message).toContain("Respuesta inválida");
    expect(stub).toHaveBeenCalledTimes(1);
  });

  it("test_ac_015_wrong_shape_is_weather_error", async () => {
    const stub = vi.fn(() => Promise.resolve('{"current":{"temperature_2m":"hot"}}'));
    const error = await fetchWeather(PLACE, "c", stub).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(WeatherError);
    expect((error as Error).message).toContain("Respuesta inválida");
  });

  it("test_ac_015_rejection_is_wrapped", async () => {
    const stub = vi.fn(() => Promise.reject(new Error("x")));
    const error = await fetchWeather(PLACE, "c", stub).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(WeatherError);
    expect(stub).toHaveBeenCalledTimes(1);
  });

  it("test_ac_015_icon_mapping", () => {
    expect(weatherIcon(0, true)).toBe("sun");
    expect(weatherIcon(1, false)).toBe("moon");
    expect(weatherIcon(3, true)).toBe("cloud");
    expect(weatherIcon(45, true)).toBe("fog");
    expect(weatherIcon(61, true)).toBe("rain");
    expect(weatherIcon(73, true)).toBe("snow");
    expect(weatherIcon(86, true)).toBe("snow");
    expect(weatherIcon(95, true)).toBe("storm");
  });

  it("test_ac_015_icon_svg_constants", () => {
    expect(WEATHER_ICON_SVG.sun.indexOf("<svg")).toBe(0);
  });
});
