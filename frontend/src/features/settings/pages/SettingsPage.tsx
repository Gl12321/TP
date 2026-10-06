import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../../app/session";
import { useWorkspace } from "../../../app/workspace";
import { get } from "../../../shared/api/client";

import {
  ErrorState,
  Loading,
  PageHeader,
  Panel,
  roleLabels,
  StatusBadge,
} from "../../../shared/ui/Common";

import { initials } from "../../../shared/ui/format";

import { SecurityPanel } from "../components/SecurityPanel";
import { CreateWorkspace } from "../components/CreateWorkspace";
import { MembersPanel } from "../components/MembersPanel";
import { StoreDirectory } from "../components/StoreDirectory";
import { roleDescriptions } from "../model/roles";

export function SettingsPage() {
  const workspace = useWorkspace();
  const { session } = useSession();
  const [search, setSearch] = useSearchParams();
  const tabs = [
    ["profile", "Мой доступ"],
    ...(workspace.can("members:manage")
      ? [
          ["members", "Команда"],
          ["stores", "Справочник точек"],
        ]
      : []),
  ];
  const tab = tabs.find(([key]) => key === search.get("tab"))?.[0] ?? "profile";
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) =>
      get<{ status: string; worker_ready: boolean }>("/health", signal),
    refetchInterval: 30_000,
    enabled: tab === "profile",
  });
  return (
    <>
      <PageHeader
        eyebrow={workspace.name}
        title="Настройки пространства"
        description="Участники, полномочия и справочник точек. Роль определяет действия, область — доступные данные."
        actions={<CreateWorkspace />}
      />
      <nav className="page-tabs" aria-label="Разделы настроек">
        {tabs.map(([key, label]) => (
          <button
            type="button"
            key={key}
            className={tab === key ? "active" : ""}
            aria-current={tab === key ? "page" : undefined}
            onClick={() =>
              setSearch((previous) => {
                const next = new URLSearchParams(previous);
                next.set("tab", key);
                return next;
              })
            }
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "members" && workspace.can("members:manage") ? (
        <MembersPanel />
      ) : tab === "stores" && workspace.can("members:manage") ? (
        <StoreDirectory />
      ) : (
        <div className="two-column">
          <Panel title="Ваша учётная запись">
            <div className="panel-body">
              <div className="account-summary">
                <span className="avatar large">
                  {initials(session?.user.name ?? "")}
                </span>
                <div>
                  <h3>{session?.user.name}</h3>
                  <p>{session?.user.email}</p>
                </div>
              </div>
              <dl className="metadata-list">
                <div>
                  <dt>Пространство</dt>
                  <dd>{workspace.name}</dd>
                </div>
                <div>
                  <dt>Роль</dt>
                  <dd>{roleLabels[workspace.role]}</dd>
                </div>
                <div>
                  <dt>Область</dt>
                  <dd>
                    {workspace.all_stores
                      ? "Все точки пространства"
                      : `Назначено точек: ${workspace.store_ids.length}`}
                  </dd>
                </div>
                <div>
                  <dt>Ассистент</dt>
                  <dd>
                    {workspace.can("assistant:use")
                      ? "Доступен в вашей области данных"
                      : "Доступ не назначен"}
                  </dd>
                </div>
              </dl>
              <p className="body-copy">{roleDescriptions[workspace.role]}</p>
            </div>
          </Panel>
          <Panel title="Доступность приложения">
            <div className="panel-body">
              {health.isPending ? (
                <Loading />
              ) : health.error ? (
                <ErrorState
                  error={health.error}
                  retry={() => void health.refetch()}
                />
              ) : (
                <>
                  <div className="service-row">
                    <span>Приложение</span>
                    <StatusBadge
                      status={
                        health.data?.status === "ok" ? "ready" : "waiting"
                      }
                    >
                      {health.data?.status === "ok"
                        ? "Доступно"
                        : "Проверяется"}
                    </StatusBadge>
                  </div>
                  <div className="service-row">
                    <span>Обработка запросов</span>
                    <StatusBadge
                      status={health.data?.worker_ready ? "ready" : "waiting"}
                    >
                      {health.data?.worker_ready ? "Готова" : "Ожидает запуска"}
                    </StatusBadge>
                  </div>
                  <p className="subtle-note">
                    Сохранённые отчёты и обсуждения доступны независимо от
                    готовности модели. Новые вопросы обрабатываются в очереди.
                  </p>
                </>
              )}
            </div>
          </Panel>
          <SecurityPanel />
        </div>
      )}
    </>
  );
}
