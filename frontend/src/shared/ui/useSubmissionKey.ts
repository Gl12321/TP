import { useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { keys } from "../api/queries";
import { useDraft } from "./useDraft";

type Submission = { fingerprint: string; key: string };

export function useSubmissionKey(name: string) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const draftName = `submission:${name}`;
  const cacheKey = keys.resource(id, "draft", draftName);
  const [, store] = useDraft<Submission | null>(draftName, null);
  const keyFor = (body: unknown) => {
    const fingerprint = JSON.stringify(body);
    const current = client.getQueryData<Submission | null>(cacheKey);
    if (current?.fingerprint === fingerprint) return current.key;
    const key = crypto.randomUUID();
    store({ fingerprint, key });
    return key;
  };
  const clearKey = () => store(null);
  return { keyFor, clearKey };
}
