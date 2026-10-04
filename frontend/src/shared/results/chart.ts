import type { Result } from "../api/contracts";

export const numericType = (type: string) =>
  /numeric|decimal|int|float|double|real/i.test(type);

export function chartColumns(result: Result) {
  const measures = result.columns.flatMap((column, index) =>
    numericType(column.type) ? [{ ...column, index }] : [],
  );
  const dimensions = result.columns.flatMap((column, index) =>
    !numericType(column.type) && !/bool|json|bytea|array/i.test(column.type)
      ? [{ ...column, index }]
      : [],
  );
  return { measures, dimensions };
}

export function chartRows(
  result: Result,
  labelIndex: number,
  valueIndex: number,
  limit = 50,
) {
  return result.rows.slice(0, limit).map((row, index) => {
    const raw = row[valueIndex];
    const text = raw === null || raw === undefined ? null : String(raw);
    const parsed =
      text !== null && /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)
        ? Number(text)
        : NaN;
    return {
      index,
      label:
        row[labelIndex] === null || row[labelIndex] === undefined
          ? "Без значения"
          : String(row[labelIndex]),
      raw: text,
      value: Number.isFinite(parsed) ? parsed : null,
    };
  });
}

export function chartScale(values: (number | null)[]) {
  const actual = values.filter((value): value is number => value !== null);
  let min = Math.min(0, ...actual),
    max = Math.max(0, ...actual);
  if (min === max) max = min + 1;
  const magnitude = Math.max(Math.abs(min), Math.abs(max), 1);
  return {
    min,
    max,
    position: (value: number) =>
      (value / magnitude - min / magnitude) /
      (max / magnitude - min / magnitude),
  };
}
