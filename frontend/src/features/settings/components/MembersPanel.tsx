import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { useWorkspace } from "../../../app/workspace";
import { get, workspacePath } from "../../../shared/api/client";
import type { Member } from "../../../shared/api/contracts";
import { keys } from "../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Panel,
  roleLabels,
  StatusBadge,
} from "../../../shared/ui/Common";
import { Icon } from "../../../shared/ui/Icon";
import { initials } from "../../../shared/ui/format";
import { InvitationsPanel } from "./InvitationsPanel";

import { MemberDialog } from "./MemberDialog";

export function MembersPanel() {
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: keys.resource(workspace.id, "members"),
    queryFn: ({ signal }) =>
      get<Member[]>(workspacePath(workspace.id, "/members"), signal),
  });
  const [editing, setEditing] = useState<Member | null>(null);
  const [invite, setInvite] = useState(false);
  const [search, setSearch] = useState("");
  const filtered =
    query.data?.filter((member) =>
      `${member.name} ${member.email}`
        .toLocaleLowerCase("ru")
        .includes(search.toLocaleLowerCase("ru")),
    ) ?? [];
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Команда пространства</h2>
          <p>Назначайте роль и область каждой учётной записи отдельно.</p>
        </div>
        <Button icon="plus" onClick={() => setInvite(true)}>
          Добавить участника
        </Button>
      </div>
      <Panel>
        <div className="list-toolbar">
          <div className="search-input">
            <Icon name="search" size={17} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Имя или почта"
              aria-label="Найти участника"
            />
          </div>
          <span className="list-count">{filtered.length} участников</span>
        </div>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : filtered.length ? (
          <div
            className="table-scroll"
            role="region"
            aria-label="Участники пространства"
            tabIndex={0}
          >
            <table className="data-table">
              <caption className="sr-only">
                Команда и назначенные полномочия
              </caption>
              <thead>
                <tr>
                  <th>Участник</th>
                  <th>Роль</th>
                  <th>Область данных</th>
                  <th>Доступ</th>
                  <th>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((member) => (
                  <tr key={member.id}>
                    <td>
                      <div className="person-cell">
                        <span className="avatar">{initials(member.name)}</span>
                        <div>
                          <strong>{member.name}</strong>
                          <span>{member.email}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      {roleLabels[member.role]}
                      {member.owner && (
                        <span className="cell-subtitle">
                          Владелец пространства
                        </span>
                      )}
                    </td>
                    <td>
                      {!member.data_access
                        ? "Аналитика закрыта"
                        : member.all_stores
                          ? "Все точки"
                          : `${member.store_ids.length} назначенных точек`}
                    </td>
                    <td>
                      <StatusBadge
                        status={member.active ? "ready" : "inactive"}
                      >
                        {member.active ? "Активен" : "Отключён"}
                      </StatusBadge>
                    </td>
                    <td>
                      {!member.owner && (
                        <Button
                          variant="quiet"
                          onClick={() => setEditing(member)}
                        >
                          Изменить
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon="search"
            title="Участники не найдены"
            description="Измените поисковый запрос."
          />
        )}
      </Panel>
      <InvitationsPanel open={invite} close={() => setInvite(false)} />
      {editing && (
        <MemberDialog member={editing} close={() => setEditing(null)} />
      )}
    </>
  );
}
