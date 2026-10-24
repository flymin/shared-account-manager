"""Benchmark CLI checks without Docker, application dependencies or database access."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import benchmark_queries as benchmark


class BenchmarkTests(unittest.TestCase):
    def test_existing_container_option_is_rejected_before_execution(self):
        with (
            patch(
                "sys.argv",
                [
                    "benchmark_queries.py",
                    "--reset-test-data",
                    "--runner",
                    "example-test-container",
                ],
            ),
            patch.object(benchmark.subprocess, "run") as run,
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(SystemExit) as stopped,
        ):
            benchmark.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unrecognized arguments: --runner", errors.getvalue())
        run.assert_not_called()

    def test_explicit_reset_is_required_before_execution(self):
        with (
            patch("sys.argv", ["benchmark_queries.py"]),
            patch.object(benchmark.subprocess, "run") as run,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            benchmark.main()
        self.assertEqual(stopped.exception.code, 2)
        run.assert_not_called()

    def test_current_source_is_mounted_read_only_for_an_isolated_process(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(benchmark, "ROOT", Path(directory)),
            patch(
                "sys.argv",
                [
                    "benchmark_queries.py",
                    "--reset-test-data",
                    "--accounts",
                    "10",
                    "--repeat",
                    "2",
                ],
            ),
            patch.object(benchmark.subprocess, "run") as run,
        ):
            benchmark.main()
            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(Path(directory) / "ops/compose.sh"))
            self.assertIn("--rm", command)
            self.assertIn("DB_NAME=account_manager_test", command)
            self.assertIn(f"{Path(directory) / 'backend'}:/app:ro", command)
            self.assertEqual(run.call_args.kwargs["cwd"], Path(directory))
            self.assertEqual(
                json.loads(run.call_args.kwargs["input"]),
                {"accounts": 10, "repeat": 2},
            )


if __name__ == "__main__":
    unittest.main()
