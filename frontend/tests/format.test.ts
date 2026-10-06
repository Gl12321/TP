import { expect, it } from "vitest";
import { compareDecimal, decimal, money } from "../src/shared/ui/format";
import { csvCell, resultCsv } from "../src/shared/results/export";

it("preserves financial precision and distinguishes missing data from zero", () => {
  expect(decimal("9007199254740993.995")).toBe(
    "9\u202f007\u202f199\u202f254\u202f740\u202f994",
  );
  expect(decimal("-0.005")).toBe("−0,01");
  expect(decimal("0.000000000123456", Infinity)).toBe("0,000000000123456");
  expect(money(null)).toBe("—");
  expect(money("0")).toBe("0 ₽");
  expect(compareDecimal("9007199254740993.1", "9007199254740993.2")).toBe(-1);
});

it("neutralizes CSV formulas without corrupting numeric values or quoting", () => {
  for (const value of ["=SUM(A1:A9)", " \t+cmd", "@SUM(1)", "-1+2"])
    expect(csvCell(value)).toBe(`"'${value}"`);
  const csv = resultCsv({
    columns: [
      { name: "=header", type: "text" },
      { name: "sum", type: "numeric" },
    ],
    rows: [['a;"b\nc', "-9007199254740993.123456789"]],
    truncated: true,
    row_count: 1,
  });
  expect(csv).toContain('"\'=header"');
  expect(csv).toContain('"a;""b\nc"');
  expect(csv).toContain('"-9007199254740993.123456789"');
});
