import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, workspacePath } from "../../../shared/api/client";
import { activeRun } from "../../../shared/api/contracts";
import type { Run } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";

export function useRun(
  workspaceId: string,
  runId: string | null,
  initial?: Run,
) {
  const client = useQueryClient();
  const [connection, setConnection] = useState<"connected" | "reconnecting">(
    "connected",
  );
  const query = useQuery({
    queryKey: keys.resource(workspaceId, "run", runId),
    queryFn: ({ signal }) =>
      get<Run>(workspacePath(workspaceId, `/runs/${runId}`), signal),
    enabled: Boolean(runId),
    initialData: initial,
    refetchInterval: (value) =>
      value.state.data && activeRun(value.state.data.status) ? 2500 : false,
  });
  const running = Boolean(query.data && activeRun(query.data.status));
  useEffect(() => {
    if (!runId || !running || typeof EventSource === "undefined") return;
    let sequence = -1;
    const source = new EventSource(
      `/api/v1${workspacePath(workspaceId, `/runs/${runId}/events`)}`,
      { withCredentials: true },
    );
    source.onopen = () => setConnection("connected");
    source.onerror = () => setConnection("reconnecting");
    const update = (event: MessageEvent<string>) => {
      let data: { sequence?: number; status?: string };
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (typeof data.sequence !== "number" || data.sequence <= sequence)
        return;
      sequence = data.sequence;
      void client.invalidateQueries({
        queryKey: keys.resource(workspaceId, "run", runId),
      });
      void client.invalidateQueries({
        queryKey: keys.resource(workspaceId, "conversation"),
      });
    };
    source.addEventListener("update", update as EventListener);
    return () => {
      source.close();
    };
  }, [workspaceId, runId, running, client]);
  return { ...query, connection };
}
