from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sql_agent.adapters.memory import available_memory
from sql_agent.query import QueryError
from sql_agent.adapters.llama import LLMClient


class AvailableMemoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.memberships = self.root / "process-cgroup"
        self.host_available = 10_000
        mock = SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(available=self.host_available)
        )
        patched = patch.dict(sys.modules, {"psutil": mock})
        patched.start()
        self.addCleanup(patched.stop)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")

    def available(self):
        return available_memory(cgroup_root=self.root, process_cgroup=self.memberships)

    def test_host_fallback_when_cgroups_are_unavailable(self):
        self.assertEqual(self.available(), self.host_available)

    def test_v2_uses_remaining_limit_and_keeps_host_bound(self):
        self.write("memory.max", 8_000)
        self.write("memory.current", 3_000)
        self.assertEqual(self.available(), 5_000)
        self.host_available = 2_000
        self.assertEqual(self.available(), 2_000)

    def test_v1_memory_controller(self):
        self.write("memory/memory.limit_in_bytes", 9_000)
        self.write("memory/memory.usage_in_bytes", 4_000)
        self.assertEqual(self.available(), 5_000)

    def test_exhausted_or_lowered_limit_returns_zero(self):
        self.write("memory.max", 2_000)
        self.write("memory.current", 3_000)
        self.assertEqual(self.available(), 0)
        self.write("memory.max", 0)
        self.assertEqual(self.available(), 0)

    def test_unlimited_invalid_and_incomplete_cgroups_keep_host_fallback(self):
        self.write("memory.current", 3_000)
        for limit in ("max", "invalid", -1):
            with self.subTest(limit=limit):
                self.write("memory.max", limit)
                self.assertEqual(self.available(), self.host_available)
        self.write("memory/memory.limit_in_bytes", 9223372036854771712)
        self.write("memory/memory.usage_in_bytes", 9223372036854770712)
        self.assertEqual(self.available(), self.host_available)
        self.write("memory.max", 8_000)
        self.write("memory.current", -1)
        self.assertEqual(self.available(), self.host_available)
        (self.root / "memory.current").unlink()
        self.assertEqual(self.available(), self.host_available)

    def test_nested_group_respects_its_parent_remaining_limit(self):
        self.write("process-cgroup", "0::/work/app\n")
        self.write("work/memory.max", 8_000)
        self.write("work/memory.current", 7_000)
        self.write("work/app/memory.max", 6_000)
        self.write("work/app/memory.current", 2_000)
        self.assertEqual(self.available(), 1_000)

    def test_nested_v1_ignores_other_controllers(self):
        self.write("process-cgroup", "3:cpu:/unused\n4:memory:/work/app\n")
        self.write("memory/work/app/memory.limit_in_bytes", 8_000)
        self.write("memory/work/app/memory.usage_in_bytes", 3_000)
        self.write("memory/unused/memory.limit_in_bytes", 1)
        self.write("memory/unused/memory.usage_in_bytes", 1)
        self.assertEqual(self.available(), 5_000)

    def test_malformed_memberships_do_not_escape_root(self):
        self.write("process-cgroup", "invalid\n0::/../outside\n0::relative\n")
        self.assertEqual(self.available(), self.host_available)

    def test_llm_reserves_model_bytes_and_free_memory_within_cgroup(self):
        client = LLMClient.__new__(LLMClient)
        client.min_free_memory_mb = 1
        self.host_available = 10 * 1024 * 1024
        self.write("memory.max", 3 * 1024 * 1024)
        self.write("memory.current", 1024 * 1024)
        with patch("sql_agent.adapters.llama.available_memory", self.available):
            client._check_memory(reserve_bytes=1024 * 1024)
            with self.assertRaises(QueryError) as caught:
                client._check_memory(reserve_bytes=1024 * 1024 + 1)
        self.assertEqual(caught.exception.code, "memory_limit")


if __name__ == "__main__":
    unittest.main()
