import { useMutation, useQuery } from "@tanstack/react-query";
import { get, post } from "../shared/api/client";
import type { AuthStatus, Session } from "../shared/api/contracts";
import { useSession } from "./session";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
  Loading,
  ErrorState,
} from "../shared/ui/Common";
import { Icon } from "../shared/ui/Icon";

export function AuthPage() {
  const { authenticate } = useSession();
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => get<AuthStatus>("/auth/status"),
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (body: Record<string, string>) =>
      post<Session>(
        status.data?.bootstrap_required ? "/auth/bootstrap" : "/auth/login",
        body,
      ),
    onSuccess: authenticate,
  });
  const setup = status.data?.bootstrap_required;
  return (
    <main className="auth-layout">
      <section className="auth-story">
        <a href="/" className="brand">
          <span className="brand-mark">р</span>
          <span>
            разбор<span className="brand-sub">аналитика сети</span>
          </span>
        </a>
        <div>
          <p className="eyebrow">От данных к решению</p>
          <h1>
            Вся сеть.
            <br />
            Каждая точка.
            <br />
            <span>Общая картина.</span>
          </h1>
          <p>
            Сравнивайте показатели, получайте таблицы по обычному вопросу и
            разбирайте изменения вместе с командой.
          </p>
          <div className="auth-benefits">
            <span>
              <Icon name="stores" /> Данные в своей области
            </span>
            <span>
              <Icon name="assistant" /> Вопрос → готовая таблица
            </span>
            <span>
              <Icon name="cases" /> Обсуждения с основаниями
            </span>
          </div>
        </div>
        <p className="auth-foot">
          Рабочее пространство для руководителей, аналитиков и команд точек.
        </p>
      </section>
      <section className="auth-form-area">
        <div className="auth-card">
          <div className="auth-lock">
            <Icon name={setup ? "plus" : "lock"} />
          </div>
          <h2>{setup ? "Первое рабочее пространство" : "С возвращением"}</h2>
          <p>
            {setup
              ? "Создайте учётную запись руководителя. Данные и участников можно добавить после входа."
              : "Войдите, чтобы продолжить работу с данными вашей сети."}
          </p>
          {status.isPending ? (
            <Loading />
          ) : status.error ? (
            <ErrorState
              error={status.error}
              retry={() => void status.refetch()}
            />
          ) : (
            <Form
              onSubmit={(form) => {
                const body: Record<string, string> = {
                  email: formText(form, "email"),
                  password: String(new FormData(form).get("password") ?? ""),
                };
                if (setup) {
                  body.name = formText(form, "name");
                  body.workspace_name = formText(form, "workspace_name");
                  if (status.data.bootstrap_token_required)
                    body.bootstrap_token = formText(form, "bootstrap_token");
                }
                mutation.mutate(body);
              }}
            >
              {setup && (
                <>
                  <Field label="Ваше имя">
                    <input
                      name="name"
                      autoComplete="name"
                      required
                      maxLength={120}
                    />
                  </Field>
                  <Field label="Название сети">
                    <input
                      name="workspace_name"
                      required
                      maxLength={160}
                      placeholder="Например, Кофейные истории"
                    />
                  </Field>
                </>
              )}
              <Field label="Электронная почта">
                <input
                  name="email"
                  type="email"
                  autoComplete="username"
                  required
                  maxLength={254}
                  placeholder="name@company.ru"
                />
              </Field>
              <Field
                label="Пароль"
                hint={setup ? "Не менее 12 символов." : undefined}
              >
                <input
                  name="password"
                  type="password"
                  autoComplete={setup ? "new-password" : "current-password"}
                  required
                  minLength={setup ? 12 : undefined}
                  maxLength={256}
                />
              </Field>
              {setup && status.data.bootstrap_token_required && (
                <Field
                  label="Ключ первоначальной настройки"
                  hint="Указан администратором при развёртывании приложения."
                >
                  <input
                    name="bootstrap_token"
                    type="password"
                    autoComplete="off"
                    required
                  />
                </Field>
              )}
              <InlineError error={mutation.error} />
              <Button
                type="submit"
                loading={mutation.isPending}
                className="full-width"
              >
                {setup ? "Создать пространство" : "Войти"}
                <Icon name="arrow" size={17} />
              </Button>
            </Form>
          )}
          <p className="auth-note">
            Доступ к источникам и точкам определяется вашей учётной записью.
          </p>
        </div>
      </section>
    </main>
  );
}
