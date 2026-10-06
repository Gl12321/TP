import { Link } from "react-router-dom";
import { useWorkspace } from "../../../app/workspace";
import { useSession } from "../../../app/session";
import { useMetrics, useSources, useStores } from "../../../shared/api/queries";
import { ErrorState, Loading } from "../../../shared/ui/Common";
import { Icon, type IconName } from "../../../shared/ui/Icon";
import "../styles/setup.css";

type SetupStep = {
  title: string;
  description: string;
  detail: string;
  icon: IconName;
  complete: boolean;
  action: string;
  path?: string;
  responsibility: string;
};

export function WorkspaceSetup() {
  const workspace = useWorkspace();
  const { session } = useSession();
  const stores = useStores(workspace.id);
  const sources = useSources(workspace.id);
  const metrics = useMetrics(workspace.id);
  const queries = [stores, sources, metrics];
  const failed = queries.find((query) => query.error);
  if (failed) {
    return (
      <ErrorState
        error={failed.error}
        retry={() => void Promise.all(queries.map((query) => query.refetch()))}
      />
    );
  }
  if (queries.some((query) => query.isPending)) {
    return <Loading label="Проверяем, что уже настроено…" />;
  }

  const activeStores = stores.data?.filter((store) => store.active) ?? [];
  const readySources =
    sources.data?.filter(
      (source) =>
        source.enabled &&
        source.status === "ready" &&
        (source.reader_ids == null ||
          source.reader_ids.includes(session?.user.id ?? "")) &&
        (source.permitted_table_count ?? 0) > 0,
    ) ?? [];
  const usableMetrics =
    metrics.data?.filter((metric) =>
      readySources.some((source) => source.id === metric.source_id),
    ) ?? [];
  const hasStores = activeStores.length > 0;
  const hasSource = readySources.length > 0;
  const base = `/w/${workspace.id}`;
  const steps: SetupStep[] = [
    {
      title: "Добавьте точки сети",
      description:
        "Точки связывают продажи, планы и ответственность сотрудников. Их коды должны совпадать с кодами в вашей базе.",
      detail: hasStores
        ? `Доступных действующих точек: ${activeStores.length}`
        : "В вашей области пока нет действующих точек",
      icon: "stores",
      complete: hasStores,
      action: hasStores ? "Открыть справочник" : "Добавить точки",
      path: workspace.can("members:manage")
        ? `${base}/settings?tab=stores`
        : undefined,
      responsibility:
        "Точки и область сотрудника назначает администратор пространства.",
    },
    {
      title: "Подключите данные",
      description:
        "Проверьте соединение с PostgreSQL, выберите нужные таблицы и поля, затем укажите, кому разрешено их читать.",
      detail: hasSource
        ? `Готовых профилей данных: ${readySources.length}`
        : sources.data?.length
          ? "Проверьте подключение и разрешите таблицы для аналитики"
          : "Доступного настроенного источника пока нет",
      icon: "data",
      complete: hasSource,
      action: hasSource ? "Проверить подключения" : "Настроить источник",
      path: workspace.can("sources:manage")
        ? `${base}/data?tab=sources`
        : undefined,
      responsibility:
        "Подключение и доступ к профилям настраивает администратор.",
    },
    {
      title: "Согласуйте первый показатель",
      description:
        "Определите, как считается выручка или другой показатель: какое поле суммировать, по какой дате и для какой точки.",
      detail: usableMetrics.length
        ? `Показателей с готовым источником: ${usableMetrics.length}`
        : "Определение связывает бизнес-название с полями вашей базы",
      icon: "overview",
      complete: hasStores && usableMetrics.length > 0,
      action: "Определить показатель",
      path:
        workspace.can("metrics:write") && hasSource && hasStores
          ? `${base}/data?tab=metrics`
          : undefined,
      responsibility:
        !hasSource || !hasStores
          ? "Сначала нужны доступные точки и настроенный источник."
          : "Согласуйте определение с аналитиком пространства.",
    },
  ];
  const completed = steps.filter((step) => step.complete).length;
  const next = steps.find((step) => !step.complete && step.path);

  return (
    <section className="workspace-setup" aria-labelledby="setup-title">
      <div className="setup-intro">
        <div>
          <span className="setup-eyebrow">Первый расчёт по вашим данным</span>
          <h2 id="setup-title">Подготовим пространство к работе</h2>
          <p>
            Точки, источник и определение показателя — три основы вашей
            аналитики. После настройки здесь появятся реальные результаты,
            которые можно сравнивать и обсуждать с командой.
          </p>
        </div>
        <div className="setup-progress">
          <strong>
            {completed}
            <span> / {steps.length}</span>
          </strong>
          <span>шагов готово</span>
          <progress
            value={completed}
            max={steps.length}
            aria-label="Готовность первоначальной настройки"
          />
        </div>
      </div>
      <ol className="setup-steps">
        {steps.map((step, index) => (
          <li
            key={step.title}
            className={`setup-step${step.complete ? " complete" : ""}${step === next ? " current" : ""}`}
            aria-current={step === next ? "step" : undefined}
          >
            <div className="setup-step-top">
              <span className="setup-step-icon">
                <Icon name={step.complete ? "check" : step.icon} size={22} />
              </span>
              <span>{step.complete ? "Готово" : `Шаг ${index + 1}`}</span>
            </div>
            <h3>{step.title}</h3>
            <p>{step.description}</p>
            <div className="setup-step-detail">{step.detail}</div>
            <div className="setup-step-action">
              {step.path ? (
                <Link
                  className={`button ${step === next ? "primary" : "secondary"}`}
                  to={step.path}
                >
                  {step.action}
                  <Icon name="arrow" size={17} />
                </Link>
              ) : (
                <p>{step.responsibility}</p>
              )}
            </div>
          </li>
        ))}
      </ol>
      <div className="setup-after">
        <Icon name="cases" size={22} />
        <div>
          <strong>Дальше — работа с результатом</strong>
          <p>
            Сравните точки в обзоре, задайте вопрос ассистенту или сохраните
            наблюдение в разбор. Планы и приглашение команды можно настроить
            после первого проверенного расчёта.
          </p>
        </div>
        {!next && !workspace.can("members:manage") && (
          <Link
            className="button secondary"
            to={`${base}/settings?tab=profile`}
          >
            Проверить свой доступ
          </Link>
        )}
      </div>
    </section>
  );
}
