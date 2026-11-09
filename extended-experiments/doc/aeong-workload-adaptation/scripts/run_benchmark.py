#!/usr/bin/env python3
"""PETGraphDB 时态查询评测（README §5-3 协议）：每请求 1 次预热 + N 次稳态，报 median/p95。

单工作线程串行（对齐 AeonG 单线程评测口径），逐请求测量客户端墙时。
用法: run_benchmark.py --bolt bolt://server:17687 --query-dir queries --patterns q1_mix,q2_mix
"""
import argparse
import glob
import json
import statistics
import time

from neo4j import GraphDatabase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolt", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--query-dir", required=True)
    ap.add_argument("--patterns", required=True,
                    help="逗号分隔，如 q1_mix,q2_mix,q3_mix,q4_mix")
    ap.add_argument("--steady", type=int, default=5)
    ap.add_argument("--tag", default="bench")
    args = ap.parse_args()
    if args.steady < 1:
        ap.error("--steady must be positive")

    drv = GraphDatabase.driver(args.bolt, auth=("neo4j", args.password))
    report = {"tag": args.tag, "bolt": args.bolt, "steady": args.steady, "groups": {}}

    with drv.session() as s:
        for pat in args.patterns.split(","):
            pat = pat.strip()
            files = sorted(glob.glob(f"{args.query_dir}/{pat}.txt")) or \
                    sorted(glob.glob(f"{args.query_dir}/{pat}*.txt"))
            if not files:
                raise FileNotFoundError(f"No query files match {pat!r} in {args.query_dir}")
            for path in files:
                name = path.rsplit("/", 1)[-1][:-4]
                with open(path) as f:
                    queries = [l.strip() for l in f if l.strip()]
                warm = []
                steady = []
                failures = 0
                for q in queries:
                    t0 = time.perf_counter()
                    try:
                        s.run(q).consume()
                    except Exception as e:  # noqa: BLE001
                        failures += 1
                        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:120]}")
                        continue
                    warm.append(time.perf_counter() - t0)
                    for _ in range(args.steady):
                        t0 = time.perf_counter()
                        try:
                            s.run(q).consume()
                        except Exception as e:  # noqa: BLE001
                            failures += 1
                            print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:120]}")
                            continue
                        steady.append(time.perf_counter() - t0)
                if steady:
                    report["groups"][name] = {
                        "queries": len(queries),
                        "failures": failures,
                        "warm_median_s": round(statistics.median(warm), 6),
                        "steady_median_s": round(statistics.median(steady), 6),
                        "steady_p95_s": round(
                            sorted(steady)[max(0, int(0.95 * len(steady)) - 1)], 6),
                        "steady_mean_s": round(statistics.mean(steady), 6),
                    }
                else:
                    report["groups"][name] = {"queries": len(queries),
                                              "failures": failures, "error": "all failed"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    drv.close()
    if any(g.get("error") or g.get("failures", 0) for g in report["groups"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
