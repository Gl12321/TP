import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSession } from "./session";
import { WorkspaceProvider, useWorkspace, workspaceStart } from "./workspace";
import { get, post, workspacePath } from "../shared/api/client";
import type {
  Capability,
  Notification,
  Workspace,
} from "../shared/api/contracts";
import { keys } from "../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Modal,
  roleLabels,
} from "../shared/ui/Common";
import { Icon } from "../shared/ui/Icon";
import type { IconName } from "../shared/ui/Icon";
import { dateTime, initials } from "../shared/ui/format";

const navigation: {
  path: string;
  label: string;
  icon: IconName;
  capability: Capability[];
}[] = [
  {
    path: "overview",
    label: "Обзор",
    icon: "overview",
    capability: ["analytics:read"],
  },
  {
    path: "stores",
    label: "Точки",
    icon: "stores",
    capability: ["analytics:read"],
  },
  {
    path: "assistant",
    label: "Ассистент",
    icon: "assistant",
    capability: ["assistant:use"],
  },
  {
    path: "reports",
    label: "Отчёты",
    icon: "reports",
    capability: ["analytics:read"],
  },
  {
    path: "cases",
    label: "Разборы",
    icon: "cases",
    capability: ["analytics:read"],
  },
  {
    path: "data",
    label: "Данные",
    icon: "data",
    capability: ["sources:manage", "analytics:read"],
  },
];

export function AppShell() {
  const { session } = useSession();
  const { workspaceId } = useParams();
  const client = useQueryClient();
  const previous = useRef(workspaceId);
  const workspace = session?.workspaces.find((item) => item.id === workspaceId);
  useEffect(() => {
    if (previous.current !== workspaceId) {
      const old = previous.current;
      void client.cancelQueries({ queryKey: ["workspace", old] });
      client.removeQueries({ queryKey: ["workspace", old] });
      previous.current = workspaceId;
    }
  }, [workspaceId, client]);
  if (!workspace)
    return (
      <div className="standalone">
        <EmptyState
          icon="lock"
          title="Пространство недоступно"
          description="Оно не входит в вашу область доступа. Выберите доступное рабочее пространство."
          action={
            <Link className="button primary" to="/">
              На главную
            </Link>
          }
        />
      </div>
    );
  return (
    <WorkspaceProvider workspace={workspace}>
      <ShellContent
        key={`${workspace.id}:${workspace.role}:${workspace.all_stores}:${workspace.store_ids.join(",")}:${workspace.capabilities.join(",")}`}
        workspace={workspace}
      />
    </WorkspaceProvider>
  );
}

function ShellContent({ workspace }: { workspace: Workspace }) {
  const { session, logout } = useSession();
  const { can } = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [logoutError, setLogoutError] = useState<Error | null>(null);
  const main = useRef<HTMLElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const notifications = useQuery({
    queryKey: keys.resource(workspace.id, "notifications"),
    queryFn: ({ signal }) =>
      get<Notification[]>(
        workspacePath(workspace.id, "/notifications"),
        signal,
      ),
    refetchInterval: 30_000,
  });
  const unread =
    notifications.data?.filter((item) => !item.read_at).length ?? 0;
  const active = navigation.find((item) =>
    location.pathname.includes(`/${item.path}`),
  );
  useEffect(() => {
    setMobileOpen(false);
    main.current?.focus({ preventScroll: true });
  }, [location.pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sidebar.current?.querySelector<HTMLElement>("a,button,select")?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButton.current?.focus();
      }
      if (event.key !== "Tab") return;
      const elements = sidebar.current?.querySelectorAll<HTMLElement>(
        "a[href],button:not(:disabled),select:not(:disabled)",
      );
      if (!elements?.length) return;
      const first = elements[0],
        last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", keydown);
    };
  }, [mobileOpen]);
  return (
    <div className={`app-shell ${mobileOpen ? "nav-open" : ""}`}>
      <a className="skip-link" href="#main-content">
        К содержимому
      </a>
      {mobileOpen && (
        <button
          className="mobile-scrim"
          aria-label="Закрыть навигацию"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <aside className="sidebar" ref={sidebar}>
        <Link
          className="brand"
          to={`/w/${workspace.id}/${workspaceStart(workspace)}`}
        >
          <span className="brand-mark">р</span>
          <span>
            разбор<span className="brand-sub">аналитика сети</span>
          </span>
        </Link>
        <div className="workspace-switch">
          <label htmlFor="workspace-select">Рабочее пространство</label>
          <select
            id="workspace-select"
            value={workspace.id}
            onChange={(event) =>
              navigate(
                `/w/${event.target.value}/${workspaceStart(session!.workspaces.find((item) => item.id === event.target.value)!)}`,
              )
            }
          >
            {session?.workspaces.map((item) => (
              <option value={item.id} key={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        <nav className="main-nav" aria-label="Основная навигация">
          {navigation
            .filter((item) => item.capability.some(can))
            .map((item) => (
              <NavLink
                to={{
                  pathname: `/w/${workspace.id}/${item.path}`,
                  search: location.search,
                }}
                key={item.path}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
                {item.path === "assistant" && (
                  <span className="nav-tag">SQL</span>
                )}
              </NavLink>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="scope-note">
            <Icon name="lock" size={15} />
            <div>
              <strong>
                {workspace.all_stores
                  ? "Вся доступная сеть"
                  : "Назначенные точки"}
              </strong>
              <span>Область данных проверяется сервером</span>
            </div>
          </div>
          <NavLink className="settings-link" to={`/w/${workspace.id}/settings`}>
            <Icon name="settings" size={18} />
            Настройки
          </NavLink>
          <div className="profile">
            <span className="avatar">{initials(session?.user.name ?? "")}</span>
            <div>
              <strong>{session?.user.name}</strong>
              <span>{roleLabels[workspace.role]}</span>
            </div>
            <button
              className="icon-button"
              aria-label="Выйти"
              title="Выйти"
              onClick={() => {
                void logout().catch((error) => setLogoutError(error));
              }}
            >
              <Icon name="logout" size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              ref={menuButton}
              aria-label="Открыть навигацию"
              onClick={() => setMobileOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <span>{workspace.name}</span>
            <Icon name="chevron" size={13} />
            <strong>{active?.label ?? "Настройки"}</strong>
          </div>
          <div className="topbar-actions">
            {can("assistant:use") && active?.path !== "assistant" && (
              <Link
                className="assistant-shortcut"
                to={`/w/${workspace.id}/assistant${location.search}`}
              >
                <Icon name="assistant" size={17} />
                <span>Вопрос к данным</span>
              </Link>
            )}
            <button
              className="icon-button notification-button"
              aria-label={`Уведомления${unread ? `, непрочитанных: ${unread}` : ""}`}
              onClick={() => setNotificationsOpen(true)}
            >
              <Icon name="bell" />
              {unread > 0 && (
                <span className="notification-count">
                  {unread > 9 ? "9+" : unread}
                </span>
              )}
            </button>
          </div>
        </header>
        <main
          id="main-content"
          className="main-content"
          tabIndex={-1}
          ref={main}
        >
          {logoutError && <ErrorState error={logoutError} />}
          <Outlet />
        </main>
        <footer className="app-footer">
          <span>Разбор · аналитика вашей сети</span>
          <span>Данные доступны в пределах ваших полномочий</span>
        </footer>
      </div>
      <NotificationDialog
        workspaceId={workspace.id}
        open={notificationsOpen}
        close={() => setNotificationsOpen(false)}
        query={notifications}
      />
    </div>
  );
}

function NotificationDialog({
  workspaceId,
  open,
  close,
  query,
}: {
  workspaceId: string;
  open: boolean;
  close: () => void;
  query: ReturnType<typeof useQuery<Notification[]>>;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: (id: string) =>
      post(workspacePath(workspaceId, `/notifications/${id}/read`)),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: keys.resource(workspaceId, "notifications"),
      }),
  });
  return (
    <Modal
      open={open}
      onOpenChange={(value) => !value && close()}
      title="Уведомления"
      description="Ответы и изменения в доступных вам разборах."
    >
      <div className="modal-body">
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} />
        ) : !query.data?.length ? (
          <EmptyState
            icon="bell"
            title="Пока нет уведомлений"
            description="Здесь появятся вопросы коллег и новые ответы в ваших разборах."
          />
        ) : (
          <div className="notification-list">
            {query.data.map((item) => (
              <article
                className={`notification-item ${!item.read_at ? "unread" : ""}`}
                key={item.id}
              >
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.body}</p>
                  <time>{dateTime(item.created_at)}</time>
                </div>
                <div className="inline-actions">
                  {item.case_id && (
                    <Button
                      variant="quiet"
                      onClick={() => {
                        mutation.mutate(item.id);
                        close();
                        navigate(`/w/${workspaceId}/cases/${item.case_id}`);
                      }}
                    >
                      Открыть
                    </Button>
                  )}
                  {!item.read_at && (
                    <Button
                      variant="quiet"
                      loading={mutation.isPending}
                      onClick={() => mutation.mutate(item.id)}
                      aria-label={`Прочитано: ${item.title}`}
                      icon="check"
                    />
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
        {mutation.error && <ErrorState error={mutation.error} />}
      </div>
    </Modal>
  );
}
