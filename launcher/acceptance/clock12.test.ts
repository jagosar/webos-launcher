import { describe, it, expect } from "vitest";
import { formatClockWithMode, formatClock } from "../src/clock";

const DATE = "lunes, 26 de octubre";

describe("AC-012 · 12-hour clock", () => {
  it("test_ac_012_12h_mode", () => {
    expect(formatClockWithMode(new Date(2026, 9, 26, 19, 5), "12")).toEqual({ time: "7:05", meridiem: "PM", date: DATE });
    expect(formatClockWithMode(new Date(2026, 9, 26, 0, 30), "12")).toEqual({ time: "12:30", meridiem: "AM", date: DATE });
    expect(formatClockWithMode(new Date(2026, 9, 26, 12, 0), "12")).toEqual({ time: "12:00", meridiem: "PM", date: DATE });
  });

  it("test_ac_012_24h_mode", () => {
    expect(formatClockWithMode(new Date(2026, 9, 26, 19, 5), "24")).toEqual({ time: "19:05", meridiem: "", date: DATE });
    expect(formatClockWithMode(new Date(2026, 9, 26, 0, 30), "24")).toEqual({ time: "00:30", meridiem: "", date: DATE });
    expect(formatClockWithMode(new Date(2026, 9, 26, 12, 0), "24")).toEqual({ time: "12:00", meridiem: "", date: DATE });
  });

  it("test_ac_012_does_not_pad_12h_hour", () => {
    expect(formatClockWithMode(new Date(2026, 9, 26, 7, 5), "12").time).not.toBe("07:05");
  });

  it("test_ac_012_formatClock_unchanged", () => {
    expect(formatClock(new Date(2026, 9, 26, 7, 5))).toEqual({ time: "07:05", date: DATE });
  });
});
