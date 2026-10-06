import type { CatalogTable } from "../../../../shared/api/contracts";

export type PolicyDraft = {
  schema: string;
  name: string;
  enabled: boolean;
  columns: string[];
  store_column: string | null;
  shared: boolean;
};

export function policyDraft(tables: CatalogTable[]): PolicyDraft[] {
  return tables.map((table) => ({
    schema: table.schema,
    name: table.name,
    enabled: Boolean(table.policy),
    columns: table.policy?.columns ?? [],
    store_column: table.policy?.store_column ?? null,
    shared: table.policy?.shared ?? false,
  }));
}

export function validatePolicies(draft: PolicyDraft[]): string | null {
  for (const table of draft.filter((item) => item.enabled)) {
    if (!table.columns.length)
      return `Выберите разрешённые поля в ${table.schema}.${table.name}.`;
    if (
      !table.shared &&
      (!table.store_column || !table.columns.includes(table.store_column))
    )
      return `Укажите разрешённое поле кода точки в ${table.schema}.${table.name}.`;
  }
  return null;
}
