import { formatClock, formatDate, formatDateTime, setDateTimePrefs } from "./format";

const ISO = "2026-07-17T18:49:15+02:00";

describe("date & time preferences", () => {
  afterEach(() => localStorage.clear());

  it("iso date + 24h with seconds", () => {
    setDateTimePrefs("iso", "24h-seconds");
    expect(formatDate(ISO)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(formatClock(ISO)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(formatDateTime(ISO)).toContain(", ");
  });

  it("european date + 12h without seconds", () => {
    setDateTimePrefs("eu", "12h");
    expect(formatDate(ISO)).toMatch(/^\d{2}\.\d{2}\.\d{4}$/);
    expect(formatClock(ISO)).toMatch(/AM|PM/i);
    expect(formatClock(ISO)).not.toMatch(/:\d{2}:\d{2}/);
  });

  it("changing the preference takes effect immediately", () => {
    setDateTimePrefs("iso", "24h");
    const before = formatDate(ISO);
    setDateTimePrefs("us", "24h");
    expect(formatDate(ISO)).not.toBe(before);
    expect(formatDate(ISO)).toMatch(/^\d{2}\/\d{2}\/\d{4}$/);
  });
});
