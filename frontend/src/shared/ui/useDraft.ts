import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { keys } from "../api/queries";

export function useDraft<T>(name: string, initial: T) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const key = keys.resource(id, "draft", name);
  const query = useQuery<T>({
    queryKey: key,
    queryFn: () => initial,
    initialData: initial,
    enabled: false,
    gcTime: Infinity,
  });
  const update = (value: T) => client.setQueryData(key, value);
  return [query.data ?? initial, update] as const;
}
