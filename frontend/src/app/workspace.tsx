import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type { Capability, Workspace } from "../shared/api/contracts";
import { monthRange, previousMonth } from "../shared/ui/format";

const WorkspaceContext = createContext<Workspace | null>(null);
export function workspaceStart(workspace: Workspace) {
  if (
    !workspace.capabilities.includes("analytics:read") ||
    workspace.role === "admin"
  )
    return "settings";
  if (workspace.role === "analyst") return "reports";
  if (workspace.role === "store_manager" && workspace.store_ids.length === 1)
    return `stores/${workspace.store_ids[0]}`;
  return "overview";
}
export function WorkspaceProvider({
  workspace,
  children,
}: {
  workspace: Workspace;
  children: ReactNode;
}) {
  return (
    <WorkspaceContext.Provider value={workspace}>
      {children}
    </WorkspaceContext.Provider>
  );
}
export function useWorkspace() {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error("WorkspaceProvider is missing");
  return {
    ...workspace,
    can: (capability: Capability) =>
      workspace.capabilities.includes(capability),
  };
}
export function useFilters() {
  const [search, setSearch] = useSearchParams();
  const month = /^\d{4}-(0[1-9]|1[0-2])$/.test(search.get("month") ?? "")
    ? search.get("month")!
    : previousMonth();
  const storeIds = (search.get("stores") ?? "").split(",").filter(Boolean);
  const metricId = search.get("metric") ?? "";
  const set = (values: Record<string, string | null>) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      Object.entries(values).forEach(([key, value]) =>
        value ? next.set(key, value) : next.delete(key),
      );
      return next;
    });
  return { month, ...monthRange(month), storeIds, metricId, set };
}
