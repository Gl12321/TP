import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../../../app/workspace";
import { post, workspacePath } from "../../../shared/api/client";
import type { Case, MeasurementRequest } from "../../../shared/api/contracts";
import { keys, useStores } from "../../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  InlineError,
  Modal,
} from "../../../shared/ui/Common";
import { useDraft } from "../../../shared/ui/useDraft";
import { useSubmissionKey } from "../../../shared/ui/useSubmissionKey";

export function CaseCreateDialog({
  close,
  storeId,
  title = "",
  description = "",
  measurement,
}: {
  close: () => void;
  storeId?: string;
  title?: string;
  description?: string;
  measurement?: MeasurementRequest;
}) {
  const workspace = useWorkspace();
  const stores = useStores(workspace.id);
  const navigate = useNavigate();
  const client = useQueryClient();
  const draftName = `case-create:${storeId ?? "all"}:${measurement ? JSON.stringify(measurement) : "manual"}`;
  const empty = { title, description, store_id: storeId ?? "" };
  const [draft, update] = useDraft(draftName, empty);
  const { keyFor, clearKey } = useSubmissionKey(draftName);
  const selectedStore = stores.data?.find(
    (store) => store.id === draft.store_id,
  );
  const mutation = useMutation({
    mutationFn: (body: object) =>
      post<Case>(workspacePath(workspace.id, "/cases"), {
        ...body,
        idempotency_key: keyFor(body),
      }),
    onSuccess: (item) => {
      clearKey();
      update(empty);
      void client.invalidateQueries({
        queryKey: keys.resource(workspace.id, "cases"),
      });
      close();
      navigate(`/w/${workspace.id}/cases/${item.id}`);
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && !mutation.isPending && close()}
      title="Новый разбор"
      description="Выберите одну точку и опишите предмет обсуждения. Вопрос сотруднику можно адресовать после сохранения."
    >
      <div className="modal-body">
        <Form
          onSubmit={() => {
            if (!draft.title.trim() || !selectedStore || mutation.isPending)
              return;
            mutation.mutate({
              title: draft.title.trim(),
              description: draft.description.trim(),
              store_ids: [draft.store_id],
              ...(measurement ? { measurement } : {}),
            });
          }}
        >
          <Field label="Точка">
            <select
              name="store_id"
              required
              value={draft.store_id}
              onChange={(event) =>
                update({ ...draft, store_id: event.target.value })
              }
              disabled={
                Boolean(measurement && storeId) ||
                stores.isPending ||
                mutation.isPending
              }
            >
              <option value="" disabled>
                {stores.isPending ? "Загружаем точки…" : "Выберите точку"}
              </option>
              {draft.store_id && !selectedStore && !stores.isPending && (
                <option value={draft.store_id} disabled>
                  Точка недоступна
                </option>
              )}
              {stores.data?.map((store) => (
                <option key={store.id} value={store.id}>
                  {store.name} · {store.city}
                </option>
              ))}
            </select>
          </Field>
          {measurement && (
            <p className="info-note">
              При сохранении будет зафиксирован расчёт выбранного показателя за
              период {measurement.date_from} — {measurement.date_to}: факт,
              план, предыдущий период и действующее определение. Последующие
              изменения источника не перепишут это основание.
            </p>
          )}
          <Field label="Название разбора">
            <input
              name="title"
              value={draft.title}
              onChange={(event) =>
                update({ ...draft, title: event.target.value })
              }
              disabled={mutation.isPending}
              required
              maxLength={180}
              placeholder="Что нужно выяснить?"
            />
          </Field>
          <Field
            label="Основание и контекст"
            hint="Свободное описание не изменяет данные источника и не доказывает причину."
          >
            <textarea
              name="description"
              value={draft.description}
              onChange={(event) =>
                update({ ...draft, description: event.target.value })
              }
              disabled={mutation.isPending}
              rows={4}
              maxLength={4000}
              placeholder="Укажите период, наблюдение и что требует проверки."
            />
          </Field>
          <InlineError error={mutation.error ?? stores.error} />
          <div className="form-actions">
            <Button
              variant="secondary"
              onClick={close}
              disabled={mutation.isPending}
            >
              Отмена
            </Button>
            <Button
              type="submit"
              loading={mutation.isPending}
              disabled={!selectedStore || !draft.title.trim()}
            >
              Создать разбор
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
