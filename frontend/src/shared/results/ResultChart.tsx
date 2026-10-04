import { useId, useState } from "react";
import type { Result } from "../api/contracts";
import { decimal } from "../ui/format";
import { chartColumns, chartRows, chartScale } from "./chart";

export function ResultChart({ result }: { result: Result }) {
  const options = chartColumns(result);
  const [labelIndex, setLabelIndex] = useState(
    options.dimensions[0]?.index ?? 0,
  );
  const [valueIndex, setValueIndex] = useState(options.measures[0]?.index ?? 0);
  const [kind, setKind] = useState<"bar" | "line">("bar");
  const [limit, setLimit] = useState(50);
  const titleId = useId();
  const points = chartRows(result, labelIndex, valueIndex, limit);
  const scale = chartScale(points.map((point) => point.value));
  const width = 800,
    height = 280,
    left = 80,
    top = 22,
    bottom = 56;
  const plotHeight = height - top - bottom,
    plotWidth = width - left - 22;
  const step = plotWidth / Math.max(points.length, 1);
  const y = (value: number) => top + (1 - scale.position(value)) * plotHeight;
  const x = (index: number) => left + step * (index + 0.5);
  const zero = y(0);
  const path = points.reduce(
    (state, point, index) => {
      if (point.value === null) return { path: state.path, connected: false };
      return {
        path: `${state.path} ${state.connected ? "L" : "M"}${x(index)},${y(point.value)}`,
        connected: true,
      };
    },
    { path: "", connected: false },
  ).path;
  const missing = points.filter((point) => point.value === null).length;
  return (
    <div className="result-chart">
      <div className="chart-controls">
        <label>
          Подписи
          <select
            value={labelIndex}
            onChange={(event) => setLabelIndex(Number(event.target.value))}
          >
            {options.dimensions.map((column) => (
              <option key={column.index} value={column.index}>
                {column.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Значения
          <select
            value={valueIndex}
            onChange={(event) => setValueIndex(Number(event.target.value))}
          >
            {options.measures.map((column) => (
              <option key={column.index} value={column.index}>
                {column.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Вид
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as "bar" | "line")}
          >
            <option value="bar">Столбцы</option>
            <option value="line">Линия</option>
          </select>
        </label>
        {result.rows.length > 20 && (
          <label>
            Строки
            <select
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
            >
              <option value={20}>Первые 20</option>
              <option value={50}>Первые 50</option>
              <option value={100}>Первые 100</option>
            </select>
          </label>
        )}
      </div>
      <div className="chart-canvas">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-labelledby={titleId}
        >
          <title id={titleId}>
            {result.columns[valueIndex].name} по{" "}
            {result.columns[labelIndex].name}. {points.length} строк, порядок из
            SQL. Точные значения доступны в таблице.
          </title>
          {[0, 0.5, 1].map((fraction) => {
            const value = scale.min * (1 - fraction) + scale.max * fraction;
            return (
              <g key={fraction}>
                <line
                  className="chart-grid"
                  x1={left}
                  x2={width - 22}
                  y1={y(value)}
                  y2={y(value)}
                />
                <text
                  className="chart-axis"
                  x={left - 10}
                  y={y(value) + 4}
                  textAnchor="end"
                >
                  {new Intl.NumberFormat("ru-RU", {
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }).format(value)}
                </text>
              </g>
            );
          })}
          <line
            className="chart-zero"
            x1={left}
            x2={width - 22}
            y1={zero}
            y2={zero}
          />
          {kind === "line" && <path className="chart-line" d={path} />}
          {points.map((point, index) => (
            <g key={index}>
              {point.value !== null &&
                (kind === "bar" ? (
                  <rect
                    className={
                      point.value < 0 ? "chart-bar negative" : "chart-bar"
                    }
                    x={x(index) - Math.min(step * 0.65, 70) / 2}
                    y={Math.min(y(point.value), zero)}
                    width={Math.min(step * 0.65, 70)}
                    height={Math.max(1, Math.abs(y(point.value) - zero))}
                  >
                    <title>
                      {point.label}: {decimal(point.raw, Infinity)}
                    </title>
                  </rect>
                ) : (
                  <circle
                    className="chart-point"
                    cx={x(index)}
                    cy={y(point.value)}
                    r={points.length > 40 ? 2 : 4}
                  >
                    <title>
                      {point.label}: {decimal(point.raw, Infinity)}
                    </title>
                  </circle>
                ))}
              {(index % Math.max(1, Math.ceil(points.length / 7)) === 0 ||
                index === points.length - 1) && (
                <text
                  className="chart-axis"
                  x={x(index)}
                  y={height - bottom + 22}
                  textAnchor="middle"
                >
                  <title>{point.label}</title>
                  {point.label.length > 15
                    ? `${point.label.slice(0, 13)}…`
                    : point.label}
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>
      <p className="chart-note">
        {points.length < result.rows.length
          ? `Показаны первые ${points.length} из ${result.rows.length} строк. `
          : ""}
        Порядок и значения из результата; повторяющиеся подписи не объединяются.
        {missing > 0 ? ` Пропуски: ${missing}, они не считаются нулём.` : ""}
        {result.truncated
          ? " Исходная выдача ограничена — график показывает только полученную часть."
          : ""}
      </p>
    </div>
  );
}
