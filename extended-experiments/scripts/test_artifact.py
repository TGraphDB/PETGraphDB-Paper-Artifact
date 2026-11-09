"""Offline tests: python -m unittest discover -s scripts -p 'test_*.py'."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ArtifactTest(unittest.TestCase):
    def test_header_conversion_and_refuse_overwrite(self):
        prepare = load("prepare", SCRIPTS / "prepare_ldbc.py")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "converted"
            for line in prepare.HEADERS.read_text().splitlines():
                if not line.strip():
                    continue
                name = line.split(" ", 1)[0]
                path = src / (name + "_0_0.csv")
                path.parent.mkdir(parents=True, exist_ok=True)
                row = {"static/place": "1|name|url|city", "static/organisation": "1|company|name|url"}.get(name, "1|2")
                path.write_text("old header\n" + row + "\n")
            argv = ["prepare_ldbc.py", str(src), str(dst)]
            with patch.object(sys, "argv", argv):
                prepare.main()
            self.assertIn("|City", (dst / "static/place_0_0.csv").read_text())
            self.assertIn("|Company|", (dst / "static/organisation_0_0.csv").read_text())
            self.assertIn("|city", (src / "static/place_0_0.csv").read_text())
            with patch.object(sys, "argv", argv), self.assertRaises(FileExistsError):
                prepare.main()

    def test_operator_command_protocol(self):
        run = load("operators", SCRIPTS / "run_operators.py")
        with tempfile.TemporaryDirectory() as tmp:
            for suite, expected in (("micro", ["microbench.py", "microbench.py"]),
                                    ("analytics", ["graphalgos.py", "analysis_bench.py"])):
                argv = ["run_operators.py", "--uri", "bolt://localhost:17688", "--system", "neo",
                        "--rep", "1", "--suite", suite, "--output", tmp, "--confirm-disposable"]
                with patch.object(sys, "argv", argv), patch.dict(os.environ, {"DB_PASSWORD": "test-only"}), \
                     patch.object(run.subprocess, "run") as execute:
                    run.main()
                commands = [call.args[0] for call in execute.call_args_list]
                self.assertEqual([Path(c[1]).name for c in commands], expected)
                for c in commands:
                    self.assertIn("--params-file", c)
                    if "--probes" in c:
                        self.assertNotIn(c[c.index("--probes") + 1], ("param", "agg"))

    def test_temporal_operation_rewrite(self):
        rewrite = load("rewrite", SCRIPTS.parent / "doc/aeong-workload-adaptation/scripts/rewrite_op_stream.py")
        query = rewrite.rewrite("MATCH (n:User {id:12}) SET n.completion_percentage = 7;", 5)
        self.assertIn("'completion_percentage', 5, 2147483647, 7", query)
        with self.assertRaises(ValueError):
            rewrite.rewrite("unrecognized query", 6)


if __name__ == "__main__":
    unittest.main()
