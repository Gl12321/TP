import { useFilters, useWorkspace } from "../../../app/workspace";
import { useMetrics, useStores } from "../../../shared/api/queries";
import { Icon } from "../../../shared/ui/Icon";

export function ScopeFilters({ fixedStore }: { fixedStore?: string }) {
  const { id } = useWorkspace();
  const filters = useFilters();
  const stores = useStores(id);
  const metrics = useMetrics(id);
  const cities = [
    ...new Set(stores.data?.map((store) => store.city).filter(Boolean)),
  ].sort();
  const selectedCity =
    cities.find((city) => {
      const ids =
        stores.data
          ?.filter((store) => store.city === city)
          .map((store) => store.id) ?? [];
      return (
        ids.length === filters.storeIds.length &&
        ids.every((value) => filters.storeIds.includes(value))
      );
    }) ?? "";
  return (
    <div className="scope-filters">
      <div className="filter-item">
        <Icon name="calendar" size={16} />
        <label htmlFor="period-filter" className="sr-only">
          Период
        </label>
        <input
          id="period-filter"
          type="month"
          value={filters.month}
          onChange={(event) =>
            event.target.value && filters.set({ month: event.target.value })
          }
        />
      </div>
      {!fixedStore && (
        <div className="filter-item">
          <label className="sr-only" htmlFor="city-filter">
            Город
          </label>
          <select
            id="city-filter"
            value={selectedCity}
            onChange={(event) =>
              filters.set({
                stores: event.target.value
                  ? (stores.data
                      ?.filter((store) => store.city === event.target.value)
                      .map((store) => store.id)
                      .join(",") ?? null)
                  : null,
              })
            }
          >
            <option value="">Все города</option>
            {cities.map((city) => (
              <option value={city} key={city}>
                {city}
              </option>
            ))}
          </select>
        </div>
      )}
      {!fixedStore && (
        <div className="filter-item">
          <Icon name="stores" size={16} />
          <label htmlFor="store-filter" className="sr-only">
            Область точек
          </label>
          <select
            id="store-filter"
            value={
              filters.storeIds.length > 1
                ? "__multiple"
                : (filters.storeIds[0] ?? "")
            }
            onChange={(event) =>
              filters.set({ stores: event.target.value || null })
            }
          >
            <option value="">Все доступные точки</option>
            {filters.storeIds.length > 1 && (
              <option value="__multiple" disabled>
                Выбрано точек: {filters.storeIds.length}
              </option>
            )}
            {stores.data?.map((store) => (
              <option value={store.id} key={store.id}>
                {store.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="filter-item metric-filter">
        <label htmlFor="metric-filter" className="sr-only">
          Показатель
        </label>
        <select
          id="metric-filter"
          value={filters.metricId || metrics.data?.[0]?.id || ""}
          onChange={(event) => filters.set({ metric: event.target.value })}
        >
          {!metrics.data?.length && (
            <option value="">Показатель не настроен</option>
          )}
          {metrics.data?.map((metric) => (
            <option value={metric.id} key={metric.id}>
              {metric.name}
            </option>
          ))}
        </select>
      </div>
      <span className="filter-help">Условия этого экрана</span>
    </div>
  );
}
