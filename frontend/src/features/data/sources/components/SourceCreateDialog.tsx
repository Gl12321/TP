import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { get, patch, post, workspacePath } from "../../../../shared/api/client";
import type { Source, Member } from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Loading,
  Modal,
  roleLabels,
} from "../../../../shared/ui/Common";

export function SourceCreateDialog({
  source,
  close,
}: {
  source?: Source;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const [readers, setReaders] = useState<string[] | null>(
    source?.reader_ids ?? null,
  );
  const members = useQuery({
    queryKey: keys.resource(id, "members"),
    queryFn: ({ signal }) =>
      get<Member[]>(workspacePath(id, "/members"), signal),
  });
  const mutation = useMutation({
    mutationFn: (body: object) =>
      source
        ? patch<Source>(workspacePath(id, `/sources/${source.id}`), body)
        : post<Source>(workspacePath(id, "/sources"), body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.resource(id, "sources") });
      close();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={source ? `Подключение · ${source.name}` : "Подключить PostgreSQL"}
      description="Подготовьте отдельного пользователя БД с правом SELECT на отчётные таблицы. Пароль не возвращается в браузер."
    >
      <div className="modal-body">
        <Form
          onSubmit={(form) => {
            const values: Record<string, unknown> = {
              name: formText(form, "name"),
              host: formText(form, "host"),
              port: Number(formText(form, "port")),
              database: formText(form, "database"),
              username: formText(form, "username"),
              schemas: formText(form, "schemas")
                .split(",")
                .map((value) => value.trim())
                .filter(Boolean),
              ssl_mode: formText(form, "ssl_mode"),
              reader_ids: readers,
            };
            if (source) values.enabled = new FormData(form).has("enabled");
            const password = String(new FormData(form).get("password") ?? "");
            if (password) values.password = password;
            const body = source
              ? Object.fromEntries(
                  Object.entries(values).filter(
                    ([key, value]) =>
                      JSON.stringify(value) !==
                      JSON.stringify(source[key as keyof Source]),
                  ),
                )
              : values;
            if (source && !Object.keys(body).length) {
              close();
              return;
            }
            mutation.mutate(body);
          }}
        >
          <Field label="Название источника">
            <input
              name="name"
              required
              maxLength={160}
              placeholder="Продажи сети"
              defaultValue={source?.name}
            />
          </Field>
          <div className="form-grid">
            <Field label="Адрес сервера">
              <input
                name="host"
                required
                maxLength={253}
                placeholder="db.company.ru"
                autoComplete="off"
                defaultValue={source?.host}
              />
            </Field>
            <Field label="Порт">
              <input
                name="port"
                type="number"
                required
                min={1}
                max={65535}
                defaultValue={source?.port ?? 5432}
              />
            </Field>
          </div>
          <Field label="База данных">
            <input
              name="database"
              required
              maxLength={128}
              defaultValue={source?.database}
            />
          </Field>
          <div className="form-grid">
            <Field label="Пользователь для чтения">
              <input
                name="username"
                required
                maxLength={128}
                autoComplete="off"
                defaultValue={source?.username}
              />
            </Field>
            <Field
              label="Пароль пользователя БД"
              hint={
                source
                  ? "Оставьте пустым, чтобы сохранить текущий пароль."
                  : undefined
              }
            >
              <input
                name="password"
                type="password"
                required={!source}
                maxLength={1024}
                autoComplete="new-password"
              />
            </Field>
          </div>
          <Field
            label="Схемы через запятую"
            hint="Укажите только схемы, предназначенные для аналитики."
          >
            <input
              name="schemas"
              required
              placeholder="reporting"
              defaultValue={source?.schemas.join(", ")}
            />
          </Field>
          <Field label="Защита соединения">
            <select
              name="ssl_mode"
              defaultValue={source?.ssl_mode ?? "require"}
            >
              <option value="require">TLS обязателен</option>
              <option value="verify-full">
                TLS с проверкой сертификата и имени
              </option>
              <option value="disable">
                Без TLS · только доверенная локальная сеть
              </option>
            </select>
          </Field>
          <Field
            label="Кто читает этот профиль"
            hint="Доступ к подключению и право читать его данные назначаются отдельно. Область точек каждого участника сохраняется."
          >
            <select
              value={readers === null ? "all" : "selected"}
              onChange={(event) =>
                setReaders(event.target.value === "all" ? null : [])
              }
            >
              <option value="all">Все участники с доступом к данным</option>
              <option value="selected">Только выбранные участники</option>
            </select>
          </Field>
          {readers !== null && (
            <div className="scope-options">
              {members.isPending ? (
                <Loading />
              ) : members.error ? (
                <InlineError error={members.error} />
              ) : (
                members.data
                  ?.filter((member) => member.active)
                  .map((member) => (
                    <label className="check-label" key={member.user_id}>
                      <input
                        type="checkbox"
                        checked={readers.includes(member.user_id)}
                        onChange={(event) =>
                          setReaders(
                            event.target.checked
                              ? [...readers, member.user_id]
                              : readers.filter(
                                  (value) => value !== member.user_id,
                                ),
                          )
                        }
                      />
                      <span>
                        {member.name}
                        <small>{roleLabels[member.role]}</small>
                      </span>
                    </label>
                  ))
              )}
              {!readers.length && (
                <p className="subtle-note">
                  Никто не сможет читать этот профиль, пока вы не выберете
                  участников.
                </p>
              )}
            </div>
          )}
          {source && (
            <>
              <label className="check-label">
                <input
                  name="enabled"
                  type="checkbox"
                  defaultChecked={source.enabled}
                />
                <span>Источник доступен для работы</span>
              </label>
              <p className="subtle-note">
                После изменения подключения повторите проверку. Сохранённые
                результаты с изменённой областью доступа могут стать недоступны.
              </p>
            </>
          )}
          <InlineError error={mutation.error} />
          <div className="form-actions">
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" loading={mutation.isPending}>
              Сохранить подключение
            </Button>
          </div>
        </Form>
      </div>
    </Modal>
  );
}
