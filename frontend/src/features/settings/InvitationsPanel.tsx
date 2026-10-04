import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/workspace";
import { get, post, workspacePath } from "../../shared/api/client";
import { keys, useStores } from "../../shared/api/queries";
import type { Invitation, Role } from "../../shared/api/contracts";
import {
  Button,
  EmptyState,
  ErrorState,
  Field,
  Form,
  formText,
  InlineError,
  Loading,
  Modal,
  Panel,
  roleLabels,
  StatusBadge,
} from "../../shared/ui/Common";
import { dateTime } from "../../shared/ui/format";
import { useToast } from "../../shared/ui/Toast";

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

function InvitationDialog({ close }: { close: () => void }) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const stores = useStores(id);
  const toast = useToast();
  const [role, setRole] = useState<Role>("store_manager");
  const [allStores, setAllStores] = useState(false);
  const [dataAccess, setDataAccess] = useState(true);
  const [storeIds, setStoreIds] = useState<string[]>([]);
  const [validation, setValidation] = useState<string | null>(null);
  const [created, setCreated] = useState<{
    email: string;
    token: string;
    expires_at: string;
  } | null>(null);
  const mutation = useMutation({
    mutationFn: (body: object) =>
      post<{ email: string; token: string; expires_at: string }>(
        workspacePath(id, "/invitations"),
        body,
      ),
    onSuccess: (value) => {
      setCreated(value);
      void client.invalidateQueries({
        queryKey: keys.resource(id, "invitations"),
      });
    },
  });
  const url = created
    ? `${window.location.origin}/invite#${created.token}`
    : "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast("Ссылка скопирована");
    } catch {
      toast("Выделите и скопируйте ссылку вручную");
    }
  };
  return (
    <Modal
      open
      onOpenChange={(value) => !value && close()}
      title={created ? "Приглашение готово" : "Пригласить в пространство"}
      description={
        created
          ? "Передайте эту ссылку сотруднику. После закрытия окна ключ нельзя получить повторно."
          : "Сотрудник задаст свой пароль сам. Для существующего аккаунта понадобится вход с той же почтой."
      }
    >
      <div className="modal-body">
        {created ? (
          <div>
            <p className="body-copy">
              Для {created.email} · до {dateTime(created.expires_at)}
            </p>
            <Field label="Личная ссылка приглашения">
              <textarea
                readOnly
                rows={3}
                value={url}
                onFocus={(event) => event.target.select()}
              />
            </Field>
            <div className="form-actions">
              <Button variant="secondary" onClick={close}>
                Готово
              </Button>
              <Button icon="copy" onClick={() => void copy()}>
                Скопировать ссылку
              </Button>
            </div>
          </div>
        ) : (
          <Form
            onSubmit={(form) => {
              if (dataAccess && !allStores && !storeIds.length) {
                setValidation(
                  "Выберите область точек или отключите аналитику.",
                );
                return;
              }
              setValidation(null);
              mutation.mutate({
                email: formText(form, "email"),
                role,
                all_stores: allStores,
                store_ids: allStores ? [] : storeIds,
                data_access: dataAccess,
              });
            }}
          >
            <Field label="Почта сотрудника">
              <input
                name="email"
                type="email"
                required
                maxLength={254}
                autoComplete="off"
              />
            </Field>
            <Field label="Роль">
              <select
                value={role}
                onChange={(event) => setRole(event.target.value as Role)}
              >
                {Object.entries(roleLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <label className="check-label">
              <input
                type="checkbox"
                checked={dataAccess}
                onChange={(event) => setDataAccess(event.target.checked)}
              />
              <span>Доступ к аналитике и ассистенту</span>
            </label>
            {dataAccess && (
              <fieldset className="scope-fieldset">
                <legend>Область данных</legend>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={allStores}
                    onChange={(event) => setAllStores(event.target.checked)}
                  />
                  <span>Все точки, включая будущие</span>
                </label>
                {!allStores && (
                  <div className="store-choices">
                    {stores.isPending ? (
                      <Loading />
                    ) : stores.error ? (
                      <ErrorState error={stores.error} />
                    ) : stores.data?.length ? (
                      stores.data.map((store) => (
                        <label className="check-label" key={store.id}>
                          <input
                            type="checkbox"
                            checked={storeIds.includes(store.id)}
                            onChange={(event) =>
                              setStoreIds((previous) =>
                                event.target.checked
                                  ? [...previous, store.id]
                                  : previous.filter(
                                      (value) => value !== store.id,
                                    ),
                              )
                            }
                          />
                          <span>
                            {store.name}
                            <small>
                              {store.city} · {store.code}
                            </small>
                          </span>
                        </label>
                      ))
                    ) : (
                      <p className="small muted">
                        Сначала добавьте точки в справочник.
                      </p>
                    )}
                  </div>
                )}
              </fieldset>
            )}
            <InlineError error={validation ?? mutation.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={close}>
                Отмена
              </Button>
              <Button type="submit" loading={mutation.isPending}>
                Создать приглашение
              </Button>
            </div>
          </Form>
        )}
      </div>
    </Modal>
  );
}
