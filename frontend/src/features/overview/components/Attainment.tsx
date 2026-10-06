import { percent } from "../../../shared/ui/format";

export function Attainment({ value }: { value: string | number | null }) {
  if (value === null) return <span className="muted">—</span>;
  return (
    <div className="attainment">
      <span className="numeric">{percent(value)}</span>
      <span className="attainment-track" aria-hidden="true">
        <span
          style={{ width: `${Math.max(0, Math.min(100, Number(value)))}%` }}
        />
      </span>
    </div>
  );
}
