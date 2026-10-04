import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useSession } from "../../app/session";
import { get, post } from "../../shared/api/client";
import type { Session, Workspace } from "../../shared/api/contracts";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Modal,
} from "../../shared/ui/Common";

export function CreateWorkspace() {
  const [open, setOpen] = useState(false);
  const { authenticate } = useSession();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: async (name: string) => {
      const workspace = await post<Workspace>("/workspaces", { name });
      const session = await get<Session>("/auth/session");
      return { workspace, session };
    },
    onSuccess: ({ workspace, session }) => {
      authenticate(session);
      setOpen(false);
      navigate(`/w/${workspace.id}/overview`);
    },
  });
  return (
    <>
      <Button
        variant="secondary"
        icon="plus"
        onClick={() => {
          mutation.reset();
          setOpen(true);
        }}
      >
        Новое пространство
      </Button>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Отдельное рабочее пространство"
        description="Для другой сети или компании. У пространства будут собственные участники, подключения, показатели и история."
      >
        <div className="modal-body">
          <Form onSubmit={(form) => mutation.mutate(formText(form, "name"))}>
            <Field label="Название сети или компании">
              <input
                name="name"
                required
                maxLength={160}
                placeholder="Название нового пространства"
              />
            </Field>
            <InlineError error={mutation.error} />
            <div className="form-actions">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={mutation.isPending}>
                Создать пространство
              </Button>
            </div>
          </Form>
        </div>
      </Modal>
    </>
  );
}
