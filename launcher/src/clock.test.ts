import { describe, it, expect, vi } from "vitest";
import { formatClock, formatClockWithMode } from "./clock";

describe("formatClock", () => {
  it("formats a morning time with zero padding", () => {
    expect(formatClock(new Date(2026, 9, 26, 7, 5))).toEqual({
      time: "07:05",
      date: "lunes, 26 de octubre",
    });
  });

  it("formats late evening and unpadded day", () => {
    expect(formatClock(new Date(2026, 0, 4, 23, 59))).toEqual({
      time: "23:59",
      date: "domingo, 4 de enero",
    });
  });

  it("formats midnight as 00:00", () => {
    expect(formatClock(new Date(2026, 11, 2, 0, 0)).time).toBe("00:00");
  });

  it("does not depend on Intl methods", () => {
    vi.spyOn(Date.prototype, "toLocaleDateString").mockImplementation(() => {
      throw new Error("locale used");
    });
    vi.spyOn(Date.prototype, "toLocaleTimeString").mockImplementation(() => {
      throw new Error("locale used");
    });
    expect(formatClock(new Date(2026, 9, 26, 7, 5)).date).toBe("lunes, 26 de octubre");
    vi.restoreAllMocks();
  });
});

describe("formatClockWithMode", () => {
  const date = "lunes, 26 de octubre";

  it.each([
    [new Date(2026, 9, 26, 19, 5), "7:05", "PM"],
    [new Date(2026, 9, 26, 0, 30), "12:30", "AM"],
    [new Date(2026, 9, 26, 12, 0), "12:00", "PM"],
    [new Date(2026, 9, 26, 11, 59), "11:59", "AM"],
  ])("formats %s in 12 h as %s %s", (d, time, meridiem) => {
    expect(formatClockWithMode(d, "12")).toEqual({ time, meridiem, date });
  });

  it("keeps the 24 h time and an empty meridiem", () => {
    expect(formatClockWithMode(new Date(2026, 9, 26, 19, 5), "24")).toEqual({
      time: "19:05",
      meridiem: "",
      date,
    });
    expect(formatClockWithMode(new Date(2026, 9, 26, 0, 30), "24").time).toBe("00:30");
  });
});
