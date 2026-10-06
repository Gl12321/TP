import { useState } from "react";

import { useWorkspace } from "../../../app/workspace";

import type { Store } from "../../../shared/api/contracts";
import { useStores } from "../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Panel,
  StatusBadge,
} from "../../../shared/ui/Common";
import { Icon } from "../../../shared/ui/Icon";

import { StoreDialog } from "./StoreDialog";

export function StoreDirectory() {
  const { id } = useWorkspace();
  const query = useStores(id);
  const [editing, setEditing] = useState<Store | "new" | null>(null);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Справочник точек</h2>
          <p>Внешний код связывает точку с фактами из подключённой базы.</p>
        </div>
        <Button icon="plus" onClick={() => setEditing("new")}>
          Добавить точку
        </Button>
      </div>
      <Panel>
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : query.data?.length ? (
          <div className="object-list">
            {query.data.map((store) => (
              <article className="object-row" key={store.id}>
                <span className="object-symbol">
                  <Icon name="stores" />
                </span>
                <div>
                  <strong>{store.name}</strong>
                  <span>
                    {store.city} · {store.code}
                    {store.owner_name ? ` · ${store.owner_name}` : ""}
                  </span>
                </div>
                <StatusBadge status={store.active ? "ready" : "inactive"}>
                  {store.active ? "Работает" : "Неактивна"}
                </StatusBadge>
                <Button variant="quiet" onClick={() => setEditing(store)}>
                  Изменить
                </Button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="stores"
            title="Добавьте первую точку"
            description="Укажите название, город и внешний код. Затем можно назначать область участникам и сравнивать показатели."
          />
        )}
      </Panel>
      {editing && (
        <StoreDialog
          store={editing === "new" ? undefined : editing}
          close={() => setEditing(null)}
        />
      )}
    </>
  );
}
