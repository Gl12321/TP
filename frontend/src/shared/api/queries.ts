import { useQuery } from "@tanstack/react-query";
import { get, workspacePath } from "./client";
import type { Metric, Source, Store } from "./contracts";

export const keys = {
  workspace: (id: string) => ["workspace", id] as const,
  resource: (id: string, name: string, ...parameters: unknown[]) =>
    ["workspace", id, name, ...parameters] as const,
};

export function useStores(id: string) {
  return useQuery({
    queryKey: keys.resource(id, "stores"),
    queryFn: ({ signal }) => get<Store[]>(workspacePath(id, "/stores"), signal),
  });
}

export function useMetrics(id: string) {
  return useQuery({
    queryKey: keys.resource(id, "metrics"),
    queryFn: ({ signal }) =>
      get<Metric[]>(workspacePath(id, "/metrics"), signal),
  });
}

export function useSources(id: string) {
  return useQuery({
    queryKey: keys.resource(id, "sources"),
    queryFn: ({ signal }) =>
      get<Source[]>(workspacePath(id, "/sources"), signal),
  });
}
