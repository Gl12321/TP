import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useSession } from "../../../app/session";
import { post } from "../../../shared/api/client";
import type { Session } from "../../../shared/api/contracts";
import {
  Button,
  Field,
  Form,
  InlineError,
  Modal,
  Panel,
} from "../../../shared/ui/Common";
import { useToast } from "../../../shared/ui/Toast";

export function SecurityPanel() {
  const { authenticate } = useSession();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: (body: object) => post<Session>("/auth/password", body),
    onSuccess: (session) => {
      authenticate(session);
      setOpen(false);
      toast("Пароль изменён. Остальные сеансы завершены.");
    },
  });
  const revoke = useMutation({
    mutationFn: () => post("/auth/sessions/revoke"),
    onSuccess: () => toast("Остальные сеансы завершены"),
  });
  return (
    <>
      <Panel
        title="Безопасность входа"
        description="Пароль и активные сеансы вашей учётной записи."
        className="spaced"
      >
        <div className="panel-body">
          <div className="security-actions">
            <div>
              <strong>Пароль</strong>
              <p>После смены потребуется заново войти на других устройствах.</p>
            </div>
            <Button
              variant="secondary"
              onClick={() => {
                change.reset();
                setValidation(null);
                setOpen(true);
              }}
            >
              Сменить пароль
            </Button>
          </div>
          <div className="security-actions">
            <div>
              <strong>Другие устройства</strong>
              <p>
                Завершите остальные сеансы. Текущая вкладка останется открытой.
              </p>
            </div>
            <Button
              variant="quiet"
              loading={revoke.isPending}
              onClick={() => revoke.mutate()}
            >
              Завершить другие сеансы
            </Button>
          </div>
          <InlineError error={revoke.error} />
        </div>
      </Panel>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Сменить пароль"
        description="Используйте новый пароль длиной не менее 12 символов."
      >
        <div className="modal-body">
          <Form
            onSubmit={(form) => {
              const data = new FormData(form);
              const password = String(data.get("new_password") ?? "");
              if (password !== String(data.get("confirmation") ?? "")) {
                setValidation("Новые пароли не совпадают.");
                return;
              }
              setValidation(null);
              change.mutate({
                current_password: String(data.get("current_password") ?? ""),
                new_password: password,
              });
            }}
          >
            <Field label="Текущий пароль">
              <input
                name="current_password"
                type="password"
                required
                maxLength={256}
                autoComplete="current-password"
              />
            </Field>
            <Field label="Новый пароль">
              <input
                name="new_password"
                type="password"
                minLength={12}
                maxLength={256}
                required
                autoComplete="new-password"
              />
            </Field>
            <Field label="Повторите новый пароль">
              <input
                name="confirmation"
                type="password"
                minLength={12}
                maxLength={256}
                required
                autoComplete="new-password"
              />
            </Field>
            <InlineError error={validation ?? change.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={change.isPending}>
                Изменить пароль
              </Button>
            </div>
          </Form>
        </div>
      </Modal>
    </>
  );
}
