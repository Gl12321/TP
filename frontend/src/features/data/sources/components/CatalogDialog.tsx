import { useQuery } from "@tanstack/react-query";
import { useWorkspace } from "../../../../app/workspace";
import { get, workspacePath } from "../../../../shared/api/client";
import type { CatalogTable, Source } from "../../../../shared/api/contracts";
import { keys } from "../../../../shared/api/queries";
import {
  EmptyState,
  ErrorState,
  Loading,
  Modal,
} from "../../../../shared/ui/Common";

import { PolicyEditor } from "./PolicyEditor";

export function CatalogDialog({
  source,
  close,
}: {
  source: Source;
  close: () => void;
}) {
  const { id } = useWorkspace();
  const query = useQuery({
    queryKey: keys.resource(id, "catalog", source.id),
    queryFn: ({ signal }) =>
      get<CatalogTable[]>(
        workspacePath(id, `/sources/${source.id}/catalog`),
        signal,
      ),
  });
  return (
    <Modal
      open
      onOpenChange={(open) => !open && close()}
      title={`Доступ к данным · ${source.name}`}
      description="Неразрешённые таблицы и поля не участвуют в аналитике. Для фактов укажите поле, содержащее внешний код точки."
      wide
    >
      <div className="modal-body">
        {query.isPending ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : query.data?.length ? (
          <PolicyEditor
            sourceId={source.id}
            tables={query.data}
            close={close}
          />
        ) : (
          <EmptyState
            icon="data"
            title="Таблицы не найдены"
            description="Проверьте список схем и права учётной записи базы, затем повторите проверку подключения."
          />
        )}
      </div>
    </Modal>
  );
}
