import { Button, InlineError } from "./Common";

export function Pagination({
  count,
  hasMore,
  loading,
  error,
  onMore,
}: {
  count: number;
  hasMore: boolean;
  loading: boolean;
  error?: unknown;
  onMore: () => void;
}) {
  return (
    <div className="pagination-footer">
      <span className="muted small" role="status">
        Показано: {count}
      </span>
      {(hasMore || !!error) && (
        <Button variant="secondary" loading={loading} onClick={onMore}>
          {error ? "Повторить загрузку" : "Показать ещё"}
        </Button>
      )}
      <InlineError error={error} />
    </div>
  );
}
