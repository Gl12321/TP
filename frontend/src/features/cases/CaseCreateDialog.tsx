import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../../app/workspace";
import { post, workspacePath } from "../../shared/api/client";
import type { Case, MeasurementRequest } from "../../shared/api/contracts";
import { keys, useStores } from "../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Modal,
} from "../../shared/ui/Common";

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
  const mutation = useMutation({
    mutationFn: (body: object) =>
      post<Case>(workspacePath(workspace.id, "/cases"), body),
    onSuccess: (item) => {
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
      onOpenChange={(open) => !open && close()}
      title="Новый разбор"
      description="Выберите одну точку и опишите предмет обсуждения. Вопрос сотруднику можно адресовать после сохранения."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) =>
            mutation.mutate({
              title: formText(form, "title"),
              description: formText(form, "description"),
              store_ids: [
                measurement && storeId ? storeId : formText(form, "store_id"),
              ],
              ...(measurement ? { measurement } : {}),
            })
          }
        >
          <Field label="Точка">
            <select
              name="store_id"
              required
              defaultValue={storeId ?? ""}
              disabled={Boolean(measurement && storeId)}
            >
              <option value="" disabled>
                Выберите точку
              </option>
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
              defaultValue={title}
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
              defaultValue={description}
              rows={4}
              maxLength={4000}
              placeholder="Укажите период, наблюдение и что требует проверки."
            />
          </Field>
          <InlineError error={mutation.error ?? stores.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Создать разбор
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
