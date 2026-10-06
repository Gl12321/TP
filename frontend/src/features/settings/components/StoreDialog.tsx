import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useWorkspace } from "../../../app/workspace";
import { patch, post, workspacePath } from "../../../shared/api/client";
import type { Store } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Modal,
} from "../../../shared/ui/Common";

export function StoreDialog({
  store,
  close,
}: {
  store?: Store;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body: object) =>
      store
        ? patch(workspacePath(id, `/stores/${store.id}`), body)
        : post(workspacePath(id, "/stores"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.workspace(id) });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={store ? `Точка · ${store.name}` : "Добавить точку"}
      description="Внешний код должен совпадать со значением ключа точки в базе данных."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) =>
            mutation.mutate({
              name: formText(form, "name"),
              city: formText(form, "city"),
              owner_name: formText(form, "owner_name"),
              active: new FormData(form).has("active"),
              ...(!store ? { code: formText(form, "code") } : {}),
            })
          }
        >
          <Field label="Название">
            <input
              name="name"
              required
              maxLength={160}
              defaultValue={store?.name}
            />
          </Field>
          <div className="form-grid">
            <Field
              label="Внешний код"
              hint={
                store ? "Код уже используется для связи данных." : undefined
              }
            >
              <input
                name="code"
                required
                maxLength={120}
                defaultValue={store?.code}
                readOnly={Boolean(store)}
              />
            </Field>
            <Field label="Город">
              <input name="city" maxLength={160} defaultValue={store?.city} />
            </Field>
          </div>
          <Field label="Партнёр">
            <input
              name="owner_name"
              maxLength={160}
              defaultValue={store?.owner_name}
            />
          </Field>
          <label className="check-label">
            <input
              name="active"
              type="checkbox"
              defaultChecked={store?.active ?? true}
            />
            <span>Точка работает</span>
          </label>
          <InlineError error={mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
