export function CalculationDetails({
  calculation,
}: {
  calculation: {
    sql: string;
    execution: { sql: string; parameters: unknown[] } | null;
  };
}) {
  return (
    <details className="sql-details">
      <summary>SQL штатного расчёта</summary>
      <div className="sql-toolbar">
        Расчёт использует выбранное определение, период и разрешённые точки.
      </div>
      <pre tabIndex={0}>
        <code>{calculation.sql}</code>
      </pre>
      {calculation.execution && (
        <details className="execution-details">
          <summary>Выполненный запрос и параметры</summary>
          <pre tabIndex={0}>
            <code>{calculation.execution.sql}</code>
          </pre>
          <pre tabIndex={0}>
            <code>
              {JSON.stringify(calculation.execution.parameters, null, 2)}
            </code>
          </pre>
        </details>
      )}
    </details>
  );
}
