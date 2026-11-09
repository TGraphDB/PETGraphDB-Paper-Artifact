#!/usr/bin/env python3
"""Run one conventional-workload leg against a prepared disposable database.

Restore a baseline before each system/rep/suite, then run this from the client.
Output layout is accepted by analyze_3rep.py. No SSH or snapshot deletion.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "doc/neo4j-vs-petgraphdb-benchmark-v2/scripts"
PARAMS = SCRIPTS.parent / "results/params/params-sf10-frozen.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uri", required=True)
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password-env", default="DB_PASSWORD")
    ap.add_argument("--system", choices=("neo", "pet"), required=True)
    ap.add_argument("--rep", type=int, choices=(1, 2, 3), required=True)
    ap.add_argument("--suite", choices=("micro", "analytics"), required=True)
    ap.add_argument("--params-file", type=Path, default=PARAMS)
    ap.add_argument("--output", type=Path, required=True, help="new-run collection root")
    ap.add_argument("--confirm-disposable", action="store_true", help="acknowledge writes to the target database")
    args = ap.parse_args()
    if not args.confirm_disposable:
        ap.error("Only run on a restored disposable benchmark database; pass --confirm-disposable")
    password = os.environ.get(args.password_env)
    if not password:
        ap.error(f"Set {args.password_env} locally before running")
    jobs = []
    if args.suite == "micro":
        out = args.output / "results-microbench-paired"
        param_probes = "point-by-id,point-by-internal-id,expand-1hop-out,expand-1hop-in,expand-2hop,khop-3-limited,shortest-path,write-set-prop,write-create-delete"
        for kind, probes, warmup, reps in (("param", param_probes, 20, 100), ("agg", "agg-person-ip-prefix,agg-message-year", 5, 20)):
            prefix = out / f"sf10-{args.system}-rep{args.rep}-{kind}"
            jobs.append(("microbench.py", prefix, ["--probes", probes, "--warmup", str(warmup), "--reps", str(reps), "--timeout", "1800"]))
    else:
        out = args.output / "results-ga-analysis-paired"
        jobs.append(("graphalgos.py", out / f"sf10-{args.system}-garep{args.rep}",
                     ["--rounds", "1", "--bfs-seeds", "50", "--bfs-depth", "3", "--pr-iters", "20", "--wcc-max-iters", "50", "--timeout", "3600"]))
        jobs.append(("analysis_bench.py", out / f"sf10-{args.system}-arep{args.rep}",
                     ["--warmup", "5", "--reps", "100", "--timeout", "1800"]))
    # Fail before any writes if an earlier output would be overwritten.
    for _, prefix, _ in jobs:
        if list(prefix.parent.glob(prefix.name + "*")):
            ap.error(f"Output already exists: {prefix}")
    out.mkdir(parents=True, exist_ok=True)
    for script, prefix, options in jobs:
        subprocess.run([sys.executable, str(SCRIPTS / script), "--uri", args.uri,
                        "--user", args.user, "--password", password,
                        "--params-file", str(args.params_file.resolve()),
                        "--out-prefix", str(prefix.resolve()), *options], check=True)


if __name__ == "__main__":
    main()
