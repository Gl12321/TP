import type { Overview } from "../../shared/api/contracts";
import { money, percent, periodLabel } from "../../shared/ui/format";
import { Icon } from "../../shared/ui/Icon";

export function PeriodComparison({
  comparison,
  unit,
}: {
  comparison: NonNullable<Overview["comparison"]>;
  unit: string;
}) {
  const values = [comparison.previous, comparison.current].map((value) =>
    value === null ? NaN : Number(value),
  );
  const maximum = Math.max(...values);
  const showBars =
    values.every((value) => Number.isFinite(value) && value >= 0) &&
    maximum > 0;
  return (
    <section
      className="period-comparison"
      aria-label="Сравнение с предыдущим периодом"
    >
      <div className="comparison-title">
        <span className="object-symbol">
          <Icon name="overview" size={21} />
        </span>
        <div>
          <h2>Что изменилось за период</h2>
          <p>
            Сопоставимые точки: {comparison.comparable_count}. В обоих периодах
            используются одинаковые точки с данными.
          </p>
        </div>
        <strong className="comparison-change">
          {comparison.change_percent === null
            ? "—"
            : `${Number(comparison.change_percent) > 0 ? "+" : ""}${percent(comparison.change_percent)}`}
        </strong>
      </div>
      <div className="comparison-periods">
        <div>
          <span>
            Предыдущий · {periodLabel(comparison.date_from, comparison.date_to)}
          </span>
          <strong>{money(comparison.previous, unit)}</strong>
          {showBars && (
            <div className="period-bar previous" aria-hidden="true">
              <span style={{ width: `${(values[0] / maximum) * 100}%` }} />
            </div>
          )}
        </div>
        <div>
          <span>Выбранный период · те же точки</span>
          <strong>{money(comparison.current, unit)}</strong>
          {showBars && (
            <div className="period-bar" aria-hidden="true">
              <span style={{ width: `${(values[1] / maximum) * 100}%` }} />
            </div>
          )}
        </div>
      </div>
      {comparison.comparable_count === 0 && (
        <p className="subtle-note">
          Для сравнения нужны данные по одной и той же точке в обоих периодах.
          Отсутствующие значения не подменяются нулями.
        </p>
      )}
    </section>
  );
}
