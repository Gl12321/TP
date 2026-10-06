import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../app/workspace";
import { get, post, workspacePath } from "../../../shared/api/client";
import { keys } from "../../../shared/api/queries";
import type { Invitation } from "../../../shared/api/contracts";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  Loading,
  Panel,
  roleLabels,
  StatusBadge,
} from "../../../shared/ui/Common";
import { dateTime } from "../../../shared/ui/format";

import { InvitationDialog } from "./InvitationDialog";

export function InvitationsPanel({
  open,
  close,
}: {
  open: boolean;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.resource(id, "invitations"),
    queryFn: ({ signal }) =>
      get<Invitation[]>(workspacePath(id, "/invitations"), signal),
  });
  const revoke = useMutation({
    mutationFn: (invitationId: string) =>
      post(workspacePath(id, `/invitations/${invitationId}/revoke`)),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: keys.resource(id, "invitations") }),
  });
  return (
    <>
      <Panel
        title="Приглашения"
        description="Ссылка выдаётся один раз и передаётся сотруднику администратором."
        className="spaced"
      >
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : query.data?.length ? (
          <div className="object-list">
            {query.data.map((item) => {
              const status = item.accepted_at
                ? "accepted"
                : item.revoked_at
                  ? "revoked"
                  : new Date(item.expires_at).getTime() < Date.now()
                    ? "expired"
                    : "pending";
              return (
                <article className="object-row" key={item.id}>
                  <div>
                    <strong>{item.email}</strong>
                    <span>
                      {roleLabels[item.role]} · до {dateTime(item.expires_at)}
                    </span>
                  </div>
                  <StatusBadge
                    status={status === "accepted" ? "ready" : status}
                  >
                    {
                      {
                        accepted: "Принято",
                        revoked: "Отозвано",
                        expired: "Истекло",
                        pending: "Ожидает входа",
                      }[status]
                    }
                  </StatusBadge>
                  {status === "pending" && (
                    <Button
                      variant="quiet"
                      loading={revoke.isPending}
                      onClick={() => revoke.mutate(item.id)}
                    >
                      Отозвать
                    </Button>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState
            icon="send"
            title="Нет отправленных приглашений"
            description="Пригласите коллегу: он задаст пароль самостоятельно и получит назначенные полномочия."
          />
        )}
        <InlineError error={revoke.error} />
      </Panel>
      {open && <InvitationDialog close={close} />}
    </>
  );
}
