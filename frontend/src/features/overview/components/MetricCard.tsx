import { Icon } from "../../../shared/ui/Icon";

export function MetricCard({
  label,
  value,
  foot,
  icon,
}: {
  label: string;
  value: string;
  foot: string;
  icon: "overview" | "reports" | "stores";
}) {
  return (
    <section className="metric-card">
      <div className="metric-card-label">
        {label}
        <Icon name={icon} size={17} />
      </div>
      <strong>{value}</strong>
      <p>{foot}</p>
    </section>
  );
}
