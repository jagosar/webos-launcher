import type { ClockDisplay, ClockMode, ClockText } from "./types";

const WEEKDAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];
const TWO_DIGITS = 10;

function pad2(n: number): string {
  return (n < TWO_DIGITS ? "0" : "") + n;
}

export function formatClock(now: Date): ClockText {
  return {
    time: `${pad2(now.getHours())}:${pad2(now.getMinutes())}`,
    date: `${WEEKDAYS[now.getDay()]}, ${now.getDate()} de ${MONTHS[now.getMonth()]}`,
  };
}

const HOURS_12 = 12;
const NOON_HOUR = 12;

export function formatClockWithMode(now: Date, mode: ClockMode): ClockDisplay {
  const base = formatClock(now);
  if (mode === "24") {
    return { time: base.time, meridiem: "", date: base.date };
  }
  const hours = now.getHours();
  return {
    time: `${((hours + HOURS_12 - 1) % HOURS_12) + 1}:${pad2(now.getMinutes())}`,
    meridiem: hours < NOON_HOUR ? "AM" : "PM",
    date: base.date,
  };
}
