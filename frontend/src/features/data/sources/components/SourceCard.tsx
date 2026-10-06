import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { post, workspacePath } from "../../../../shared/api/client";
import type { Source } from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  Button,
  InlineError,
  Panel,
  StatusBadge,
} from "../../../../shared/ui/Common";
import { Icon } from "../../../../shared/ui/Icon";
import { dateTime } from "../../../../shared/ui/format";

export function SourceCard({
  source,
  onCatalog,
  onEdit,
}: {
  source: Source;
  onCatalog: () => void;
  onEdit: () => void;
}) {
  const { id } = useWorkspace();
  const client = useQueryClient();
  const test = useMutation({
    mutationFn: () =>
      post<Source>(workspacePath(id, `/sources/${source.id}/test`)),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: keys.resource(id, "sources") }),
  });
  const error =
    source.error &&
    (typeof source.error === "string" ? source.error : source.error.message);
  const status = !source.enabled
    ? "Отключён"
    : source.status === "ready"
      ? "Подключён"
      : source.status === "error"
        ? "Ошибка подключения"
        : "Нужна проверка";
  return (
    <Panel className="source-card">
      <div className="source-card-top">
        <span className="object-symbol">
          <Icon name="data" size={23} />
        </span>
        <StatusBadge status={source.enabled ? source.status : "inactive"}>
          {status}
        </StatusBadge>
      </div>
      <h3>{source.name}</h3>
      <p className="source-address">
        {source.host}:{source.port} / {source.database}
      </p>
      <dl className="metadata-list">
        <div>
          <dt>Схемы</dt>
          <dd>{source.schemas.join(", ")}</dd>
        </div>
        <div>
          <dt>Пользователь БД</dt>
          <dd>{source.username}</dd>
        </div>
        <div>
          <dt>Последняя проверка</dt>
          <dd>{dateTime(source.last_checked_at)}</dd>
        </div>
        <div>
          <dt>Версия каталога</dt>
          <dd>{source.catalog_version}</dd>
        </div>
        {source.permitted_table_count !== undefined && (
          <div>
            <dt>Разрешено таблиц</dt>
            <dd>{source.permitted_table_count || "Доступ ещё не настроен"}</dd>
          </div>
        )}
        <div>
          <dt>Кто читает данные</dt>
          <dd>
            {source.reader_ids == null
              ? "Все участники с доступом к данным"
              : source.reader_ids.length
                ? `Выбранные участники: ${source.reader_ids.length}`
                : "Никто — профиль закрыт"}
          </dd>
        </div>
      </dl>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <InlineError error={test.error} />
      <div className="source-actions">
        <Button
          variant="secondary"
          icon="refresh"
          loading={test.isPending}
          onClick={() => test.mutate()}
        >
          Проверить
        </Button>
        <Button
          variant="quiet"
          icon="lock"
          disabled={!source.catalog_version}
          onClick={onCatalog}
        >
          Таблицы и доступ
        </Button>
        <Button
          variant="quiet"
          onClick={onEdit}
          icon="settings"
          aria-label={`Изменить подключение ${source.name}`}
        />
      </div>
    </Panel>
  );
}
