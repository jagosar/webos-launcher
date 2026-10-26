import { describe, it, expect, vi, afterEach } from "vitest";
import { formatClock } from "../src/clock";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AC-003 · Clock text in Spanish, 24 h", () => {
  it("test_ac_003_formats_monday_morning_zero_padded", () => {
    expect(formatClock(new Date(2026, 9, 26, 7, 5))).toEqual({
      time: "07:05",
      date: "lunes, 26 de octubre",
    });
  });

  it("test_ac_003_formats_sunday_night_without_day_padding", () => {
    expect(formatClock(new Date(2026, 0, 4, 23, 59))).toEqual({
      time: "23:59",
      date: "domingo, 4 de enero",
    });
  });

  it("test_ac_003_does_not_depend_on_intl_methods", () => {
    vi.spyOn(Date.prototype, "toLocaleDateString").mockImplementation(() => {
      throw new Error("locale used");
    });
    vi.spyOn(Date.prototype, "toLocaleTimeString").mockImplementation(() => {
      throw new Error("locale used");
    });
    expect(formatClock(new Date(2026, 9, 26, 7, 5))).toEqual({
      time: "07:05",
      date: "lunes, 26 de octubre",
    });
  });

  it("test_ac_003_accented_weekdays_and_midnight", () => {
    expect(formatClock(new Date(2026, 9, 28, 0, 0))).toEqual({
      time: "00:00",
      date: "miércoles, 28 de octubre",
    });
    expect(formatClock(new Date(2026, 9, 31, 12, 30)).date).toBe("sábado, 31 de octubre");
  });
});
