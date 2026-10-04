import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import {
  chartColumns,
  chartRows,
  chartScale,
} from "../src/shared/results/chart";
import { ResultChart } from "../src/shared/results/ResultChart";
import type { Result } from "../src/shared/api/contracts";

const result: Result = {
  columns: [
    { name: "Точка", type: "text" },
    { name: "Сумма", type: "numeric" },
    { name: "Заказы", type: "int4" },
  ],
  rows: [
    ["Арбат", "9007199254740993.123456789", 5],
    ["Арбат", null, 0],
    ["Порт", "-0.009", 2],
  ],
  truncated: true,
  row_count: 3,
};

it("retains duplicate labels, missing values and exact source decimals without aggregating", () => {
  expect(chartColumns(result).measures.map((column) => column.index)).toEqual([
    1, 2,
  ]);
  const rows = chartRows(result, 0, 1);
  expect(rows.map((row) => row.label)).toEqual(["Арбат", "Арбат", "Порт"]);
  expect(rows[0].raw).toBe("9007199254740993.123456789");
  expect(rows[1].value).toBeNull();
  expect(rows[2].value).toBe(-0.009);
});
it("uses a zero baseline for negative and positive values without infinity overflow", () => {
  const scale = chartScale([-1e308, 1e308, null]);
  expect(scale.position(0)).toBe(0.5);
  expect(scale.position(1e308)).toBe(1);
  expect(chartScale([0, null]).position(0)).toBe(0);
});
it("lets a user select another measure and makes chart limitations explicit", async () => {
  render(<ResultChart result={result} />);
  expect(screen.getByRole("img")).toHaveAccessibleName(/Сумма по Точка/);
  expect(screen.getByText(/Пропуски: 1/)).toBeVisible();
  expect(screen.getByText(/выдача ограничена/)).toBeVisible();
  await userEvent
    .setup()
    .selectOptions(screen.getByRole("combobox", { name: "Значения" }), "2");
  expect(screen.getByRole("img")).toHaveAccessibleName(/Заказы по Точка/);
  expect(screen.queryByText(/Пропуски: 1/)).not.toBeInTheDocument();
});
