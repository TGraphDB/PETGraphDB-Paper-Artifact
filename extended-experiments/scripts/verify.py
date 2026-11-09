#!/usr/bin/env python3
"""Validate packaged bytes, CSV provenance, and frozen inputs without dependencies."""
import csv
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def rows(name):
    with (ROOT / "figures" / name).open(newline="") as f:
        return list(csv.DictReader(f))


def document(path, digest):
    raw = (ROOT / path).read_bytes()
    check(hashlib.sha256(raw).hexdigest() == digest, f"Source hash: {path}")
    # Historical mix-v1 includes failed-query log lines before the JSON report.
    text = raw.decode()
    return json.loads(text[text.index("{\n"):])


def pointer(data, expression):
    match = re.fullmatch(r"sum\((.+)\[0:6\]\)", expression)
    if match:
        return sum(pointer(data, match[1])[:6])
    for key in expression.strip("/").split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def close(a, b):
    check(math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12),
          f"Value differs: {a} != {b}")


def main():
    manifest = json.loads((ROOT / "MANIFEST.json").read_text())
    for name, digest in manifest["sha256"].items():
        check(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest,
              f"Packaged file changed: {name}")

    conventional = rows("ldbc-sf10.csv")
    check(len(conventional) == 459, "Expected 459 conventional observations")
    for r in conventional:
        d = document(r["source"], r["source_sha256"])
        for side in ("neo", "pet"):
            close(r[side], pointer(d, r[f"{side}_pointer"]))
        close(r["ratio"], float(r["pet"]) / float(r["neo"]))
    for r in rows("ldbc-sf10-summary.csv"):
        values = [float(r[f"rep{i}"]) for i in (1, 2, 3)]
        close(r["geomean"], math.exp(sum(map(math.log, values)) / 3))
        close(r["min"], min(values))
        close(r["max"], max(values))

    temporal = rows("aeong-workloads.csv")
    check(len(temporal) == 20, "Expected 20 temporal groups")
    for r in temporal:
        aeong = document(r["aeong_source"], r["aeong_sha256"])
        petg = document(r["petg_source"], r["petg_sha256"])
        close(r["aeong_mean_ms"], pointer(aeong, r["aeong_pointer"]))
        group = pointer(petg, r["petg_pointer"])
        check(group["failures"] == 0 and group["queries"] == 100,
              f"Unsuccessful group: {r['query']}")
        for stat in ("mean", "median", "p95"):
            close(r[f"petg_{stat}_ms"], 1000 * group[f"steady_{stat}_s"])
    for r in rows("aeong-workload-storage.csv"):
        close(r["data_mib"], int(r["data_bytes"]) / 2**20)
        check((ROOT / r["source"]).is_file(), f"Missing storage source: {r['source']}")

    for directory in ("data/results/temporal_query", "data/aeong-temporal-query"):
        queries = ROOT / "doc/aeong-workload-adaptation" / directory
        frozen = json.loads((queries / "manifest.json").read_text())
        for name, info in frozen["files"].items():
            raw = (queries / name).read_bytes()
            check(hashlib.sha256(raw).hexdigest() == info["sha256"], f"Frozen query: {name}")
            check(len(raw.splitlines()) == info["lines"], f"Query count: {name}")
    for name, info in manifest["uncompressed"].items():
        with gzip.open(ROOT / name, "rb") as f:
            raw = f.read()
        check(hashlib.sha256(raw).hexdigest() == info["sha256"], f"Operation stream: {name}")
        check(len(raw.splitlines()) == 320000, f"Operation count: {name}")
    print(f"PASS: {len(manifest['sha256'])} files, 459 conventional observations, "
          "20 temporal groups, storage conversions, and frozen Pokec inputs")


if __name__ == "__main__":
    main()
