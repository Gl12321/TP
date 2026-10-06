import { useState } from "react";

import { useWorkspace } from "../../../../app/workspace";

import type { Source } from "../../../../shared/api/contracts";
import { useSources } from "../../../../shared/api/queries";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Panel,
} from "../../../../shared/ui/Common";

import { SourceCard } from "../components/SourceCard";
import { SourceCreateDialog } from "../components/SourceCreateDialog";
import { CatalogDialog } from "../components/CatalogDialog";

export function SourcesPanel() {
  const { id } = useWorkspace();
  const sources = useSources(id);
  const [create, setCreate] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);
  const [catalogSource, setCatalogSource] = useState<Source | null>(null);
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Подключённые базы</h2>
          <p>
            Доступ только для чтения. Вы сами выбираете разрешённые таблицы и
            поля.
          </p>
        </div>
        <Button icon="plus" onClick={() => setCreate(true)}>
          Подключить PostgreSQL
        </Button>
      </div>
      <div className="setup-steps">
        <div>
          <span>01</span>
          <strong>Подключите базу</strong>
          <p>Отдельная учётная запись для чтения отчётных данных.</p>
        </div>
        <div>
          <span>02</span>
          <strong>Определите доступ</strong>
          <p>Разрешите поля и укажите связь фактов с кодами точек.</p>
        </div>
        <div>
          <span>03</span>
          <strong>Согласуйте показатели</strong>
          <p>Задайте, какие значения считать и по какой дате.</p>
        </div>
      </div>
      {sources.isPending ? (
        <Loading />
      ) : sources.error ? (
        <ErrorState
          error={sources.error}
          retry={() => void sources.refetch()}
        />
      ) : sources.data?.length ? (
        <div className="source-grid">
          {sources.data.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              onCatalog={() => setCatalogSource(source)}
              onEdit={() => setEditing(source)}
            />
          ))}
        </div>
      ) : (
        <Panel>
          <EmptyState
            icon="data"
            title="Источник ещё не подключён"
            description="Подключите отчётную базу PostgreSQL. После проверки соединения откроется каталог таблиц для настройки доступа."
            action={
              <Button
                variant="secondary"
                icon="plus"
                onClick={() => setCreate(true)}
              >
                Добавить источник
              </Button>
            }
          />
        </Panel>
      )}
      {(create || editing) && (
        <SourceCreateDialog
          source={editing ?? undefined}
          close={() => {
            setCreate(false);
            setEditing(null);
          }}
        />
      )}
      {catalogSource && (
        <CatalogDialog
          source={catalogSource}
          close={() => setCatalogSource(null)}
        />
      )}
    </>
  );
}
