const ISO_WITH_OFFSET = /^(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,3}(\d+)?)?)?(?:Z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)$/i;
const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

export const UNKNOWN_TIME_LABEL = "Unknown time";

export function parseAbsoluteTimestampMs(
  value: string | null | undefined,
): number | null {
  if (!value) return null;
  const match = ISO_WITH_OFFSET.exec(value);
  if (!match || match[0] !== value) return null;
  const [, yearText, monthText, dayText, subMillisecond] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const leapDay = month === 2 && year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  if (Number(dayText) > DAYS_IN_MONTH[month - 1] + (leapDay ? 1 : 0)) return null;

  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return null;
  return subMillisecond ? ms + Number(`0.${subMillisecond}`) : ms;
}

export function elapsedTimestampMs(
  start: string | null | undefined,
  end: string | number | null | undefined,
): number | null {
  const startMs = parseAbsoluteTimestampMs(start);
  if (startMs === null) return null;
  const endMs = typeof end === "number" ? end : parseAbsoluteTimestampMs(end);
  return endMs === null || endMs < startMs ? null : endMs - startMs;
}
