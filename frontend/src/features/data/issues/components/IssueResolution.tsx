import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { patch, workspacePath } from "../../../../shared/api/client";
import type {
  SourceIssue,
  SourceIssueDetail,
} from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  Button,
  ErrorState,
  Field,
  Form,
  InlineError,
  Panel,
} from "../../../../shared/ui/Common";

import { useDraft } from "../../../../shared/ui/useDraft";
import { useToast } from "../../../../shared/ui/Toast";
import { issueStatus } from "../model/status";
import { useSourceAdministrators } from "../hooks/useSourceAdministrators";

export function IssueResolution({
  detail,
  refresh,
}: {
  detail: SourceIssueDetail;
  refresh: () => void;
}) {
  const workspace = useWorkspace();
  const client = useQueryClient();
  const notify = useToast();
  const administrators = useSourceAdministrators(workspace.id);
  const [draft, update] = useDraft(`source-issue-resolution:${detail.id}`, {
    status: detail.status,
    assignee: detail.assignee_id ?? "",
    resolution: detail.resolution ?? "",
    dirty: false,
  });
  const status = draft.dirty ? draft.status : detail.status;
  const assignee = draft.dirty ? draft.assignee : (detail.assignee_id ?? "");
  const resolution = draft.dirty ? draft.resolution : (detail.resolution ?? "");
  const changed =
    status !== detail.status ||
    assignee !== (detail.assignee_id ?? "") ||
    (status === "resolved" && resolution.trim() !== (detail.resolution ?? ""));
  const validAssignee =
    !assignee ||
    assignee === detail.assignee_id ||
    administrators.data?.some((person) => person.id === assignee);
  const change = (values: Partial<typeof draft>) => {
    update({ status, assignee, resolution, ...values, dirty: true });
  };
  const mutation = useMutation({
    mutationFn: () =>
      patch<SourceIssue>(
        workspacePath(workspace.id, `/source-issues/${detail.id}`),
        {
          status,
          assignee_id: assignee || null,
          ...(status === "resolved" ? { resolution: resolution.trim() } : {}),
        },
      ),
    onSuccess: (saved) => {
      client.setQueryData<SourceIssueDetail>(
        keys.resource(workspace.id, "source-issue", detail.id),
        (previous) => (previous ? { ...previous, ...saved } : previous),
      );
      update({ ...draft, dirty: false });
      notify(
        saved.status === "resolved"
          ? "Решение сохранено. Автор обращения получит уведомление."
          : "Обращение обновлено.",
      );
      refresh();
    },
  });
  return (
    <Panel title="Ответственный и решение">
      <Form
        className="issue-management"
        onSubmit={() => {
          if (
            changed &&
            validAssignee &&
            !mutation.isPending &&
            (status !== "resolved" || resolution.trim())
          )
            mutation.mutate();
        }}
      >
        <Field label="Статус обращения">
          <select
            disabled={mutation.isPending}
            value={status}
            onChange={(event) =>
              change({
                status: event.target.value as typeof status,
              })
            }
          >
            {Object.entries(issueStatus).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Ответственный за источник">
          <select
            value={assignee}
            disabled={
              mutation.isPending ||
              administrators.isPending ||
              !!administrators.error
            }
            onChange={(event) => change({ assignee: event.target.value })}
          >
            <option value="">Не назначен</option>
            {!validAssignee && (
              <option value={assignee} disabled>
                Участник больше недоступен
              </option>
            )}
            {detail.assignee_id &&
              !administrators.data?.some(
                (person) => person.id === detail.assignee_id,
              ) && (
                <option value={detail.assignee_id}>
                  Прежний ответственный
                </option>
              )}
            {administrators.data?.map((person) => (
              <option value={person.id} key={person.id}>
                {person.name}
              </option>
            ))}
          </select>
        </Field>
        {status === "resolved" && (
          <Field
            label="Исправление и проверка"
            hint="Что изменили и как убедились, что проблема устранена."
          >
            <textarea
              rows={4}
              required
              disabled={mutation.isPending}
              maxLength={10000}
              value={resolution}
              onChange={(event) => change({ resolution: event.target.value })}
            />
          </Field>
        )}
        {administrators.error && (
          <ErrorState
            error={administrators.error}
            retry={() => void administrators.refetch()}
          />
        )}
        {status !== "resolved" && detail.status === "resolved" && (
          <p className="subtle-note">
            Обращение снова станет открытым для работы. Прежнее решение
            останется в истории переписки.
          </p>
        )}
        {draft.dirty && (
          <div className="issue-draft-note">
            <span>Изменения пока не отправлены.</span>
            <Button
              variant="quiet"
              disabled={mutation.isPending}
              onClick={() => update({ ...draft, dirty: false })}
            >
              Сбросить
            </Button>
          </div>
        )}
        <InlineError error={mutation.error} />
        <Button
          type="submit"
          loading={mutation.isPending}
          disabled={
            administrators.isPending ||
            !!administrators.error ||
            !changed ||
            !validAssignee ||
            (status === "resolved" && !resolution.trim())
          }
        >
          Сохранить решение
        </Button>
      </Form>
    </Panel>
  );
}
