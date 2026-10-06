import { useQuery } from "@tanstack/react-query";

import { get, workspacePath } from "../../../../shared/api/client";
import type { SourceAdministrator } from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";

export function useSourceAdministrators(id: string) {
  return useQuery({
    queryKey: keys.resource(id, "source-administrators"),
    queryFn: ({ signal }) =>
      get<SourceAdministrator[]>(
        workspacePath(id, "/source-issues/participants"),
        signal,
      ),
  });
}
