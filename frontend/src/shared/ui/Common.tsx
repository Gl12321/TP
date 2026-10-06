import * as Dialog from "@radix-ui/react-dialog";
import { cloneElement, isValidElement, useId, useRef } from "react";
import type {
  ReactNode,
  FormEvent,
  ButtonHTMLAttributes,
  HTMLAttributes,
} from "react";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { ApiError } from "../api/client";

export function Button({
  children,
  variant = "primary",
  icon,
  loading,
  className = "",
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  icon?: IconName;
  loading?: boolean;
}) {
  return (
    <button
      type="button"
      className={`button ${variant} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <span className="spinner small" />
      ) : icon ? (
        <Icon name={icon} size={17} />
      ) : null}
      {children}
    </button>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Loading({ label = "Загружаем данные…" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="spinner" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  const forbidden = error instanceof ApiError && error.status === 403;
  return (
    <div className="error-state" role="alert">
      <Icon name={forbidden ? "lock" : "alert"} />
      <div>
        <strong>
          {forbidden ? "Доступ ограничен" : "Не удалось загрузить данные"}
        </strong>
        <p>
          {error instanceof Error
            ? error.message
            : "Повторите попытку немного позже."}
        </p>
        {retry && !forbidden && (
          <Button variant="secondary" onClick={retry}>
            Повторить
          </Button>
        )}
      </div>
    </div>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="form-error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}

export function EmptyState({
  icon = "reports",
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={28} />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-heading">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className={`modal-content ${wide ? "wide" : ""}`}
          onOpenAutoFocus={() => {
            returnFocus.current =
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const trigger = returnFocus.current;
            if (trigger?.isConnected && !trigger.matches(":disabled"))
              trigger.focus({ preventScroll: true });
            else
              document
                .querySelector<HTMLElement>("main")
                ?.focus({ preventScroll: true });
          }}
        >
          <div className="modal-heading">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              {description ? (
                <Dialog.Description>{description}</Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">
                  {title}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close className="icon-button" aria-label="Закрыть">
              <Icon name="close" />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  const generatedId = useId();
  const control = isValidElement<HTMLAttributes<HTMLElement>>(children)
    ? children
    : null;
  const id = control?.props.id ?? generatedId;
  const hintId = `${generatedId}-hint`;
  const describedBy = [control?.props["aria-describedby"], hint ? hintId : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={`field ${className}`}>
      <label htmlFor={id}>{label}</label>
      {control
        ? cloneElement(control, {
            id,
            "aria-describedby": describedBy || undefined,
          })
        : children}
      {hint && <small id={hintId}>{hint}</small>}
    </div>
  );
}

export function Form({
  onSubmit,
  children,
  className = "",
}: {
  onSubmit: (form: HTMLFormElement) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <form
      className={`form ${className}`}
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        onSubmit(event.currentTarget);
      }}
    >
      {children}
    </form>
  );
}

export const formText = (form: HTMLFormElement, key: string) =>
  String(new FormData(form).get(key) ?? "").trim();
export const roleLabels: Record<string, string> = {
  director: "Руководитель сети",
  regional_manager: "Региональный менеджер",
  franchise_owner: "Франчайзи",
  store_manager: "Управляющий точки",
  analyst: "Аналитик",
  admin: "Администратор",
};
export const runLabels: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  cancel_requested: "Останавливается",
  cancelled: "Отменено",
  succeeded: "Готово",
  failed: "Ошибка",
  needs_input: "Нужно уточнение",
  rejected: "Не выполнено",
};
export function StatusBadge({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  const tone = [
    "succeeded",
    "ready",
    "connected",
    "closed",
    "done",
    "answered",
    "complete",
    "resolved",
    "ok",
  ].includes(status)
    ? "success"
    : ["failed", "error", "missing", "rejected"].includes(status)
      ? "danger"
      : [
            "queued",
            "running",
            "cancel_requested",
            "needs_input",
            "waiting",
            "pending",
            "in_progress",
          ].includes(status)
        ? "warning"
        : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <span className="badge-dot" />
      {children ?? runLabels[status] ?? status}
    </span>
  );
}
