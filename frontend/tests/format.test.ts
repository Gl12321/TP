import { describe, expect, it } from "vitest";
import {
  compareDecimal,
  decimal,
  money,
  monthRange,
  percent,
} from "../src/shared/ui/format";
import { csvCell, resultCsv } from "../src/shared/results/export";

describe("precise financial presentation", () => {
  it.each([
    ["1.999", "2"],
    ["0.009", "0,01"],
    ["-0.005", "−0,01"],
    ["-0.004", "0"],
    ["999.995", "1\u202f000"],
    ["9007199254740993.995", "9\u202f007\u202f199\u202f254\u202f740\u202f994"],
    [
      "-9007199254740993.125",
      "−9\u202f007\u202f199\u202f254\u202f740\u202f993,13",
    ],
  ])("rounds %s without floating point loss", (input, expected) =>
    expect(decimal(input)).toBe(expected),
  );
  it("preserves complete precision in SQL tables", () =>
    expect(decimal("0.000000000123456", Infinity)).toBe("0,000000000123456"));
  it("distinguishes zero from missing data", () => {
    expect(money(null)).toBe("—");
    expect(money("0")).toBe("0 ₽");
    expect(percent(null)).toBe("—");
  });
  it("sorts decimals beyond Number precision and handles null", () => {
    expect(compareDecimal("9007199254740993.1", "9007199254740993.2")).toBe(-1);
    expect(compareDecimal("-9007199254740993.1", "-9007199254740993.2")).toBe(
      1,
    );
    expect(compareDecimal("1", "1.00")).toBe(0);
    expect(compareDecimal(null, "0")).toBe(-1);
  });
  it("includes leap-year last day", () =>
    expect(monthRange("2024-02")).toEqual({
      from: "2024-02-01",
      to: "2024-02-29",
    }));
});

describe("safe CSV with exact source values", () => {
  it.each(["=SUM(A1:A9)", " \t+cmd", "@SUM(1)", "-1+2"])(
    "neutralizes spreadsheet formula %s",
    (value) => expect(csvCell(value)).toBe(`"'${value}"`),
  );
  it("preserves negative numbers, escapes quotes and retains huge decimals", () => {
    const csv = resultCsv({
      columns: [
        { name: '="header"', type: "text" },
        { name: "sum", type: "numeric" },
      ],
      rows: [['a;"b\nc', "-9007199254740993.123456789"]],
      truncated: true,
      row_count: 1,
    });
    expect(csv).toContain('"\'=\"\"header\"\""');
    expect(csv).toContain('"a;""b\nc"');
    expect(csv).toContain('"-9007199254740993.123456789"');
  });
});
