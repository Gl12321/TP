import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSession } from "../../../app/session";
import { useWorkspace } from "../../../app/workspace";
import { patch, workspacePath } from "../../../shared/api/client";
import type { Member, Role } from "../../../shared/api/contracts";
import { keys, useStores } from "../../../shared/api/queries";
import {
  Button,
  ErrorState,
  Field,
  Form,
  InlineError,
  Loading,
  Modal,
  roleLabels,
} from "../../../shared/ui/Common";

import { roleDescriptions } from "../model/roles";

export function MemberDialog({
  member,
  close,
}: {
  member: Member;
  close: () => void;
}) {
  const workspace = useWorkspace();
  const { refresh } = useSession();
  const client = useQueryClient();
  const stores = useStores(workspace.id);
  const [role, setRole] = useState<Role>(member?.role ?? "store_manager");
  const [allStores, setAllStores] = useState(member?.all_stores ?? false);
  const [storeIds, setStoreIds] = useState<string[]>(member?.store_ids ?? []);
  const [dataAccess, setDataAccess] = useState(member?.data_access ?? true);
  const [active, setActive] = useState(member?.active ?? true);
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: object) =>
      patch(workspacePath(workspace.id, `/members/${member.id}`), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(workspace.id) });
      refresh();
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={`Доступ · ${member.name}`}
      description="Изменение роли и области применяется к следующим действиям участника. Сохранённые результаты тоже проверяются по актуальным правам."
    >
      <div className="modal-body">
        <Form
          onSubmit={() => {
            if (dataAccess && !allStores && !storeIds.length) {
              setValidation(
                "Назначьте хотя бы одну точку или отключите доступ к аналитике.",
              );
              return;
            }
            setValidation(null);
            mutation.mutate({
              role,
              all_stores: allStores,
              store_ids: allStores ? [] : storeIds,
              data_access: dataAccess,
              active,
            });
          }}
        >
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
          <p className="role-description">{roleDescriptions[role]}</p>
          <label className="check-label">
            <input
              type="checkbox"
              checked={dataAccess}
              onChange={(event) => setDataAccess(event.target.checked)}
            />
            <span>
              <strong>Доступ к аналитике и ассистенту</strong>
              <small>В пределах назначенной области данных.</small>
            </span>
          </label>
          {dataAccess && (
            <fieldset className="scope-fieldset">
              <legend>Область точек</legend>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={allStores}
                  onChange={(event) => setAllStores(event.target.checked)}
                />
                <span>Все точки, включая добавленные в будущем</span>
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
                                : previous.filter((id) => id !== store.id),
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
          {member && (
            <label className="check-label">
              <input
                type="checkbox"
                checked={active}
                onChange={(event) => setActive(event.target.checked)}
              />
              <span>
                <strong>Учётная запись активна в пространстве</strong>
                <small>
                  При отключении сохранённые данные остаются, доступ сотрудника
                  прекращается.
                </small>
              </span>
            </label>
          )}
          <InlineError error={validation ?? mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить доступ
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
