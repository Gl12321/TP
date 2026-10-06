import { useQuery } from "@tanstack/react-query";

import { useFilters, useWorkspace } from "../../../app/workspace";
import { get, queryString, workspacePath } from "../../../shared/api/client";
import type { Overview } from "../../../shared/api/contracts";
import { keys, useMetrics } from "../../../shared/api/queries";

export function useOverview(fixedStore?: string) {
  const { id } = useWorkspace();
  const filters = useFilters();
  const metrics = useMetrics(id);
  const metricId = filters.metricId || metrics.data?.[0]?.id || "";
  const parameters = {
    date_from: filters.from,
    date_to: filters.to,
    metric_id: metricId,
    store_ids: fixedStore ? [fixedStore] : filters.storeIds,
  };
  const query = useQuery({
    queryKey: keys.resource(id, "overview", parameters),
    queryFn: ({ signal }) =>
      get<Overview>(
        workspacePath(id, `/overview${queryString(parameters)}`),
        signal,
      ),
    enabled: Boolean(metricId),
  });
  return { ...query, metrics, metricId };
}
