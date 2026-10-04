import { useSearchParams } from "react-router-dom";
import { useWorkspace } from "../../app/workspace";
import { PageHeader } from "../../shared/ui/Common";
import { SourcesPanel } from "./SourcesPanel";
import { MetricsPanel } from "./MetricsPanel";
import { PlansPanel } from "./PlansPanel";

export function DataPage() {
  const workspace = useWorkspace();
  const [search, setSearch] = useSearchParams();
  const tabs = [
    ...(workspace.can("sources:manage")
      ? [{ key: "sources", label: "Источники и доступ" }]
      : []),
    ...(workspace.can("analytics:read")
      ? [
          { key: "metrics", label: "Определения показателей" },
          { key: "plans", label: "Планы точек" },
        ]
      : []),
  ];
  const current =
    tabs.find((tab) => tab.key === search.get("tab"))?.key ?? tabs[0]?.key;
  return (
    <>
      <PageHeader
        eyebrow="Основание каждого расчёта"
        title="Данные и показатели"
        description="Подключения, согласованные определения и планы. Здесь команда задаёт, откуда берутся числа и что они означают."
      />
      <nav className="page-tabs" aria-label="Разделы данных">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.key}
            aria-current={current === tab.key ? "page" : undefined}
            className={current === tab.key ? "active" : ""}
            onClick={() =>
              setSearch((previous) => {
                const next = new URLSearchParams(previous);
                next.set("tab", tab.key);
                return next;
              })
            }
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {current === "sources" ? (
        <SourcesPanel />
      ) : current === "metrics" ? (
        <MetricsPanel />
      ) : current === "plans" ? (
        <PlansPanel />
      ) : null}
    </>
  );
}
