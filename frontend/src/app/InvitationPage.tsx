import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "./session";
import { post } from "../shared/api/client";
import type { Session } from "../shared/api/contracts";
import {
  Button,
  Field,
  Form,
  formText,
  InlineError,
} from "../shared/ui/Common";
import { Icon } from "../shared/ui/Icon";

export function InvitationPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { session, authenticate, logout } = useSession();
  const [existing, setExisting] = useState(false);
  const [logoutError, setLogoutError] = useState<Error | null>(null);
  const token = location.hash.slice(1);
  const login = useMutation({
    mutationFn: (body: object) => post<Session>("/auth/login", body),
    onSuccess: authenticate,
  });
  const accept = useMutation({
    mutationFn: (body: object) =>
      post<Session>("/auth/invitations/accept", body),
    onSuccess: (result) => {
      authenticate(result);
      navigate("/", { replace: true });
    },
  });
  return (
    <main className="invitation-page">
      <Link to="/" className="invitation-brand">
        <span className="brand-mark">р</span>разбор
      </Link>
      <section className="invitation-card">
        <span className="auth-lock">
          <Icon name="stores" />
        </span>
        <h1>Присоединиться к команде</h1>
        <p className="body-copy">
          Приглашение связывает вашу учётную запись с рабочим пространством и
          назначенной областью данных.
        </p>
        {!token ? (
          <>
            <p className="form-error">
              В ссылке отсутствует ключ приглашения. Откройте полную ссылку от
              администратора.
            </p>
            <Link className="button secondary" to="/">
              На страницу входа
            </Link>
          </>
        ) : session ? (
          <>
            <div className="info-note">
              Вы вошли как {session.user.name} · {session.user.email}. Почта
              должна совпадать с приглашением.
            </div>
            <InlineError error={accept.error ?? logoutError} />
            <Button
              loading={accept.isPending}
              className="full-width"
              onClick={() => accept.mutate({ token })}
            >
              Принять приглашение
            </Button>
            <Button
              variant="quiet"
              className="full-width"
              onClick={() => {
                void logout().catch((error) => setLogoutError(error));
              }}
            >
              Войти другим аккаунтом
            </Button>
          </>
        ) : (
          <>
            <div className="segmented invitation-tabs">
              <button
                type="button"
                aria-pressed={!existing}
                className={!existing ? "active" : ""}
                onClick={() => {
                  setExisting(false);
                  login.reset();
                  accept.reset();
                }}
              >
                Первый вход
              </button>
              <button
                type="button"
                aria-pressed={existing}
                className={existing ? "active" : ""}
                onClick={() => {
                  setExisting(true);
                  login.reset();
                  accept.reset();
                }}
              >
                Есть аккаунт
              </button>
            </div>
            <Form
              onSubmit={(form) => {
                const password = String(
                  new FormData(form).get("password") ?? "",
                );
                if (existing)
                  login.mutate({ email: formText(form, "email"), password });
                else
                  accept.mutate({
                    token,
                    name: formText(form, "name"),
                    password,
                  });
              }}
            >
              {existing ? (
                <Field label="Почта существующего аккаунта">
                  <input
                    name="email"
                    type="email"
                    required
                    autoComplete="username"
                    maxLength={254}
                  />
                </Field>
              ) : (
                <Field label="Ваше имя">
                  <input
                    name="name"
                    required
                    maxLength={120}
                    autoComplete="name"
                  />
                </Field>
              )}
              <Field
                label={existing ? "Пароль" : "Придумайте пароль"}
                hint={
                  existing
                    ? undefined
                    : "Не менее 12 символов. Аккаунт будет создан на почту из приглашения."
                }
              >
                <input
                  key={existing ? "current" : "new"}
                  name="password"
                  type="password"
                  required
                  minLength={existing ? undefined : 12}
                  maxLength={256}
                  autoComplete={existing ? "current-password" : "new-password"}
                />
              </Field>
              <InlineError error={login.error ?? accept.error} />
              <Button
                type="submit"
                loading={login.isPending || accept.isPending}
                className="full-width"
              >
                {existing
                  ? "Войти и проверить приглашение"
                  : "Создать аккаунт и присоединиться"}
              </Button>
            </Form>
          </>
        )}
      </section>
    </main>
  );
}
