import {
  formatClock,
  formatDate,
  formatDateTime,
  hydrateDateTimePrefs,
  setDateTimePrefs,
  subscribeDateTimePrefs,
} from "./format";

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

describe("timezone preference", () => {
  afterEach(() => localStorage.clear());

  it("renders instants in the chosen zone", () => {
    // 18:49 UTC = 20:49 in Berlin (July, DST).
    setDateTimePrefs("iso", "24h", "Europe/Berlin");
    expect(formatClock("2026-07-17T18:49:15+00:00")).toBe("20:49");
    setDateTimePrefs("iso", "24h", "UTC");
    expect(formatClock("2026-07-17T18:49:15+00:00")).toBe("18:49");
  });

  it("zone shifts can change the date", () => {
    setDateTimePrefs("iso", "24h", "Pacific/Auckland");
    // 23:30 UTC on the 17th is already the 18th in Auckland.
    expect(formatDate("2026-07-17T23:30:00+00:00")).toBe("2026-07-18");
  });

  it("plain calendar dates never shift", () => {
    setDateTimePrefs("iso", "24h", "Pacific/Auckland");
    expect(formatDate("2014-07-11")).toBe("2014-07-11");
  });
});

describe("AUDIT FP-M2: invalid timezone never crashes a render", () => {
  it("falls back to UTC for zones this ICU does not know", () => {
    localStorage.setItem("pllm.pref.timeZone", "Not/AZone");
    try {
      expect(() => formatDateTime("2026-03-07T12:00:00Z")).not.toThrow();
      expect(formatDateTime("2026-03-07T12:00:00Z")).toContain("2026");
    } finally {
      localStorage.removeItem("pllm.pref.timeZone");
    }
  });
});

describe("AUDIT FP-M1: pref changes notify subscribers", () => {
  it("hydrate and set bump the subscription", () => {
    let called = 0;
    const un = subscribeDateTimePrefs(() => {
      called += 1;
    });
    setDateTimePrefs("iso", "24h");
    hydrateDateTimePrefs({ date_format: "eu", time_format: "24h", time_zone: "UTC" });
    un();
    setDateTimePrefs("iso", "24h");
    expect(called).toBe(2); // unsubscribed calls don't count
  });
});
