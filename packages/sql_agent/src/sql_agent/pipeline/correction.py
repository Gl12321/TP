from dataclasses import dataclass


@dataclass(frozen=True)
class CorrectionPolicy:
    max_corrections: int = 3

    def should_retry(self, *, attempts: int, retryable: bool) -> bool:
        return retryable and attempts <= self.max_corrections

    @staticmethod
    def repeated(sql: str, previous: list[str]) -> bool:

        return sql.strip() in {query.strip() for query in previous}
