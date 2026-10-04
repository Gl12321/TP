from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
import json
import unittest
from uuid import UUID

from sql_agent.query import QueryError, QueryEvent, QueryResult
from sql_agent.sql.results import encode_event, json_value


class ResultTests(unittest.TestCase):
    def test_decimal_and_temporal_values_preserve_precision(self):
        number = Decimal("12345678901234567890.00123000")
        instant = datetime(2026, 9, 29, 12, 30, 45, 123456, tzinfo=timezone.utc)
        result = json_value([number, instant, date(2026, 9, 29), time(12, 30, 45, 123456)])
        self.assertEqual(
            result,
            [
                "12345678901234567890.00123000",
                "2026-09-29T12:30:45.123456+00:00",
                "2026-09-29",
                "12:30:45.123456",
            ],
        )
        self.assertEqual(Decimal(result[0]), number)

    def test_interval_uuid_and_binary_values_round_trip(self):
        identifier = UUID("12345678-1234-5678-1234-567812345678")
        interval = timedelta(days=-2, microseconds=123)
        self.assertEqual(json_value(identifier), str(identifier))
        self.assertEqual(timedelta(**json_value(interval)), interval)
        for value in (b"\x00\xff", bytearray(b"\x00\xff"), memoryview(b"\x00\xff")):
            self.assertEqual(json_value(value), {"encoding": "base64", "value": "AP8="})

    def test_nonfinite_numbers_are_strings_in_standard_json(self):
        line = encode_event([float("inf"), float("-inf"), float("nan"), Decimal("NaN")])
        self.assertEqual(json.loads(line), ["inf", "-inf", "nan", "NaN"])

    def test_events_form_one_line_and_keep_request_identity(self):
        event = QueryEvent("stage", "first-request", {"message": "Строка 1\nСтрока 2"})
        line = encode_event(event)
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(len(line.splitlines()), 1)
        decoded = json.loads(line)
        self.assertEqual(decoded["request_id"], "first-request")
        self.assertEqual(decoded["content"]["message"], "Строка 1\nСтрока 2")

    def test_dataclass_results_support_memoryview_without_deep_copy(self):
        result = QueryResult("SELECT data", ["data"], [[memoryview(b"\x00\xff")]])
        self.assertEqual(json_value(result)["rows"], [[{"encoding": "base64", "value": "AP8="}]])

    def test_size_limit_counts_utf8_bytes(self):
        with self.assertRaises(QueryError) as caught:
            encode_event("я", max_bytes=3)
        self.assertEqual(caught.exception.code, "result_too_large")
        self.assertEqual(json.loads(encode_event("я", max_bytes=8)), "я")

    def test_nested_values_enums_and_unknown_types(self):
        class State(Enum):
            READY = "ready"

        value = {"state": State.READY, "rows": [(Decimal("1.00"), None, True)]}
        self.assertEqual(json_value(value), {"state": "ready", "rows": [["1.00", None, True]]})
        with self.assertRaises(QueryError) as caught:
            json_value(object())
        self.assertEqual(caught.exception.code, "unsupported_result_type")


if __name__ == "__main__":
    unittest.main()
