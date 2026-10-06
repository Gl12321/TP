import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../workspace";
import { post, workspacePath } from "../../../shared/api/client";
import type { Notification } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Modal,
} from "../../../shared/ui/Common";
import { Icon } from "../../../shared/ui/Icon";
import { dateTime } from "../../../shared/ui/format";

export function NotificationDialog({
  workspaceId,
  open,
  close,
  query,
}: {
  workspaceId: string;
  open: boolean;
  close: () => void;
  query: ReturnType<typeof useQuery<Notification[]>>;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const { can } = useWorkspace();
  const navigationRequest = useRef<string | null>(null);
  const notificationKey = keys.resource(workspaceId, "notifications");
  const mutation = useMutation({
    mutationFn: ({ id }: { id: string; destination?: string }) =>
      post(
        workspacePath(
          workspaceId,
          `/notifications/${encodeURIComponent(id)}/read`,
        ),
      ),
    onSuccess: async (_, { id }) => {
      await client.cancelQueries({ queryKey: notificationKey });
      client.setQueryData<Notification[]>(notificationKey, (items) =>
        items?.map((item) =>
          item.id === id
            ? { ...item, read_at: new Date().toISOString() }
            : item,
        ),
      );
      void client.invalidateQueries({ queryKey: notificationKey });
    },
  });
  const dismiss = () => {
    navigationRequest.current = null;
    if (!mutation.isPending) mutation.reset();
    close();
  };
  const openDestination = (destination: string) => {
    dismiss();
    navigate(destination);
    requestAnimationFrame(() =>
      document.getElementById("main-content")?.focus({ preventScroll: true }),
    );
  };
  const markRead = (item: Notification, destination?: string) => {
    navigationRequest.current = destination ? item.id : null;
    mutation.mutate(
      { id: item.id, destination },
      {
        onSuccess: () => {
          if (destination && navigationRequest.current === item.id)
            openDestination(destination);
        },
      },
    );
  };
  return (
    <Modal
      open={open}
      onOpenChange={(value) => !value && dismiss()}
      title="Уведомления"
      description="Ответы коллег, изменения разборов и обращения по данным."
    >
      <div className="modal-body">
        {query.error && (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        )}
        {mutation.error && (
          <div className="error-state" role="alert">
            <Icon name="alert" />
            <div>
              <strong>Не удалось отметить уведомление прочитанным</strong>
              <p>{mutation.error.message}</p>
            </div>
          </div>
        )}
        {query.isPending ? (
          <Loading />
        ) : !query.data?.length && !query.error ? (
          <EmptyState
            icon="bell"
            title="Пока нет уведомлений"
            description="Здесь появятся вопросы коллег, ответы в разборах и сообщения по источникам данных."
          />
        ) : (
          <div className="notification-list">
            {query.data?.map((item) => {
              const destination = item.source_issue_id
                ? can("sources:manage") || can("analytics:read")
                  ? `/w/${workspaceId}/data?tab=issues&issue=${encodeURIComponent(item.source_issue_id)}`
                  : null
                : item.case_id && can("analytics:read")
                  ? `/w/${workspaceId}/cases/${encodeURIComponent(item.case_id)}`
                  : null;
              const pending =
                mutation.isPending && mutation.variables.id === item.id;
              return (
                <article
                  className={`notification-item ${!item.read_at ? "unread" : ""}`}
                  key={item.id}
                  aria-busy={pending}
                >
                  <div className="notification-copy">
                    <strong>{item.title}</strong>
                    <p>{item.body}</p>
                    <time dateTime={item.created_at}>
                      {dateTime(item.created_at)}
                    </time>
                  </div>
                  <div className="inline-actions">
                    {destination && (
                      <Button
                        variant="quiet"
                        disabled={mutation.isPending}
                        loading={pending && !!mutation.variables?.destination}
                        onClick={() => {
                          if (item.read_at) openDestination(destination);
                          else markRead(item, destination);
                        }}
                      >
                        Открыть
                      </Button>
                    )}
                    {!item.read_at && (
                      <Button
                        variant="quiet"
                        disabled={mutation.isPending}
                        loading={pending && !mutation.variables?.destination}
                        onClick={() => markRead(item)}
                        aria-label={`Отметить прочитанным: ${item.title}`}
                        title="Отметить прочитанным"
                        icon="check"
                      />
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
}
