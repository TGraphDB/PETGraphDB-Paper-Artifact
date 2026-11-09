#!/usr/bin/env python3
"""
图算法基准（v2，PLAN §4.4 方案 B）：BFS / PageRank / WCC 纯 Cypher 实现，
**两侧执行完全相同的查询文本**（不依赖 APOC/GDS，PETGraphDB 4.4 内核兼容）。

与微基准（microbench.py）的区别：这里跑的是"算法级"批式作业——
BFS 对 N 个种子顺序展开计数；PageRank/WCC 全图迭代直至收敛（含初始化/清理）。
只跑 SF10（PLAN 决策；SF100 纯 Cypher 迭代不现实）。

协议（对齐 2026-10-24 "先 rep1" 决策）：
  --rounds 默认 1，全部实验结束后回来补 round 2/3；
  每轮独立初始化 → 执行 → 清理临时属性（ga_*），轮间不残留数据；
  PageRank/WCC 逐迭代记录耗时与图效应量（PR 顶值 / WCC 连通分量数），
  既可对比两系统，也能检验算法结果合理性。

输出（--out-prefix 为前缀）：
  <prefix>_graphalgos.json     汇总（每轮每阶段耗时 + sanity 量）
  <prefix>_graphalgos_raw.csv  逐次执行原始记录（BFS 逐种子 / PR、WCC 逐迭代）
  graphalgos_samples_<uri>.json 种子缓存（--sample-cache 目录下，多轮复用）
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from microbench import Runner  # 复用：driver 连接 / 单查询超时 / 原始记录

DAMPING = 0.85


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def summarize(lat):
    s = sorted(lat)
    return {
        "count": len(lat),
        "mean_s": statistics.mean(lat),
        "median_s": statistics.median(lat),
        "p95_s": percentile(s, 95),
        "min_s": s[0],
        "max_s": s[-1],
        "total_s": sum(lat),
    }


# ---------------------------------------------------------------------------
# Cypher 文本（两侧通用；$ 参数由 driver 注入）
# ---------------------------------------------------------------------------

# 注意：变长路径的深度不能用参数（Cypher 禁止 MATCH pattern 里用参数 map），
# $depth 由 build_queries 在运行前按 CLI 参数字面量替换（同 microbench 的 $max_depth）。
BFS_QUERY_TPL = (
    # 变长路径展开 + WITH DISTINCT ... LIMIT 惰性短路（同 microbench khop 保护手法）
    "MATCH (p:Person {id: $id})-[:KNOWS*1..$depth]-(q) "
    "WITH DISTINCT q LIMIT $cap RETURN count(*) AS c"
)

PR_INIT = "MATCH (p:Person) SET p.ga_pr = 1.0 / $n, p.ga_tmp = 0.0 RETURN count(*) AS c"
PR_OUTDEG = "MATCH (p:Person) SET p.ga_out = size((p)-[:KNOWS]->()) RETURN count(*) AS c"
PR_ZERO = "MATCH (p:Person) SET p.ga_tmp = 0.0 RETURN count(*) AS c"
# 悬挂节点（出度 0）按 0 贡献处理（标准近似，报告注明）；out=0 时除法保护走 CASE
PR_SCATTER = (
    "MATCH (p:Person)-[:KNOWS]->(q) "
    "WITH q, sum(p.ga_pr / CASE WHEN p.ga_out = 0 THEN 1 ELSE p.ga_out END) AS inc "
    "SET q.ga_tmp = inc RETURN count(*) AS c"
)
PR_COMBINE = (
    "MATCH (p:Person) SET p.ga_pr = $base + $damping * p.ga_tmp RETURN count(*) AS c"
)
PR_TOP = "MATCH (p:Person) RETURN p.id AS id, p.ga_pr AS pr ORDER BY pr DESC LIMIT 3"
PR_CLEANUP = "MATCH (p:Person) REMOVE p.ga_pr, p.ga_tmp, p.ga_out RETURN count(*) AS c"

WCC_INIT = "MATCH (p:Person) SET p.ga_wcc = id(p) RETURN count(*) AS c"
WCC_ITER = (
    "MATCH (p:Person)-[:KNOWS]-(q) WHERE p.ga_wcc < q.ga_wcc "
    "SET q.ga_wcc = p.ga_wcc RETURN count(*) AS c"
)
WCC_COUNT = "MATCH (p:Person) RETURN count(DISTINCT p.ga_wcc) AS ncomp"
WCC_CLEANUP = "MATCH (p:Person) REMOVE p.ga_wcc RETURN count(*) AS c"


# ---------------------------------------------------------------------------
# 参数采样：BFS 种子（稀疏/稠密分层，缓存复用）
# ---------------------------------------------------------------------------

def _cache_file(args):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", args.uri).strip("_") or "default"
    path = args.sample_cache
    if path.endswith(".json"):
        return path
    return os.path.join(path, f"graphalgos_samples_{safe}.json")


def sample_seeds(runner, args):
    if getattr(args, "params_file", None):
        with open(args.params_file) as f:
            frozen = json.load(f)
        seeds = frozen["bfs_seeds"]
        half = len(seeds) // 2
        print(f"[params] 冻结参数集 {args.params_file}：BFS 种子 {len(seeds)} 个"
              f"（稀 {half} + 稠 {len(seeds) - half}）", flush=True)
        return {"bfs_seeds_sparse": seeds[:half],
                "bfs_seeds_dense": seeds[half:]}
    cache = _cache_file(args)
    if os.path.exists(cache) and not args.resample:
        print(f"[sample] 使用缓存 {cache}", flush=True)
        with open(cache) as f:
            return json.load(f)
    print(f"[sample] 在线采样 BFS 种子（scan={args.sample_scan}）...", flush=True)
    t0 = time.time()
    rows = runner.run_query(
        "MATCH (p:Person) WITH p ORDER BY rand() LIMIT $scan "
        "OPTIONAL MATCH (p)-[:KNOWS]-(p2) "
        "WITH p, count(p2) AS deg RETURN p.id AS id, deg ORDER BY deg",
        {"scan": args.sample_scan})
    persons = [(r["id"], r["deg"]) for r in rows]
    persons.sort(key=lambda x: x[1])
    n = args.bfs_seeds // 2
    sparse = [pid for pid, deg in persons if deg >= 1][:n]
    dense = [pid for pid, deg in persons[-n:]]
    data = {
        "format": 1,
        "uri": args.uri,
        "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bfs_seeds_sparse": sparse,
        "bfs_seeds_dense": dense,
    }
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    with open(cache, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[sample] 完成 sparse={len(sparse)} dense={len(dense)} "
          f"耗时 {time.time() - t0:.1f}s → {cache}", flush=True)
    return data


# ---------------------------------------------------------------------------
# 三个阶段
# ---------------------------------------------------------------------------

def phase_bfs(runner, args, seeds, rnd, raw, query):
    print(f"[bfs] round {rnd}: {len(seeds)} 个种子, depth={args.bfs_depth}, "
          f"cap={args.path_cap}", flush=True)
    lats, counts = [], []
    for pid in seeds:
        t0 = time.perf_counter()
        c, dt, err = None, 0.0, ""
        try:
            with runner.driver.session(database=runner.database) as s:
                try:
                    res = s.run(query, {"id": pid, "cap": args.path_cap},
                                timeout_=datetime.timedelta(
                                    seconds=runner.query_timeout_s))
                except TypeError:  # 旧 driver 无 timeout_
                    res = s.run(query, {"id": pid, "cap": args.path_cap})
                c = res.single()["c"]
        except Exception as e:  # noqa
            err = str(e)[:200]
        dt = time.perf_counter() - t0
        if err:
            raw.writerow(["bfs", rnd, pid, "", "", err])
            print(f"  seed={pid} ERROR {err[:120]}", flush=True)
        else:
            lats.append(dt)
            counts.append(c)
            raw.writerow(["bfs", rnd, pid, f"{dt:.3f}", c, ""])
            print(f"  seed={pid} reachable={c} {dt * 1000:.0f}ms", flush=True)
    return {
        "seeds": len(seeds),
        "depth": args.bfs_depth,
        "cap": args.path_cap,
        "reachable_min": min(counts) if counts else None,
        "reachable_median": statistics.median(counts) if counts else None,
        "reachable_max": max(counts) if counts else None,
        "lat": summarize(lats) if lats else {},
        "n_failed": len(seeds) - len(lats),
    }


def phase_pagerank(runner, args, nperson, rnd, raw):
    base = (1.0 - DAMPING) / nperson
    print(f"[pagerank] round {rnd}: N={nperson}, iters={args.pr_iters}, "
          f"damping={DAMPING}", flush=True)

    def run(q, **params):
        t0 = time.perf_counter()
        with runner.driver.session(database=runner.database) as s:
            summary = s.run(q, params,
                            timeout_=datetime.timedelta(
                                seconds=runner.query_timeout_s)).consume()
        dt = time.perf_counter() - t0
        return dt, summary.counters.properties_set

    t, _ = run(PR_INIT, n=float(nperson)); raw.writerow(["pr_init", rnd, "", f"{t:.3f}", "", ""])
    t, _ = run(PR_OUTDEG); raw.writerow(["pr_outdeg", rnd, "", f"{t:.3f}", "", ""])
    iters = []
    for i in range(args.pr_iters):
        t_zero, _ = run(PR_ZERO)
        t_scat, nset = run(PR_SCATTER)
        t_comb, _ = run(PR_COMBINE, base=base, damping=DAMPING)
        t_it = t_zero + t_scat + t_comb
        iters.append(t_it)
        raw.writerow(["pr_iter", rnd, i, f"{t_it:.3f}", nset, ""])
        print(f"  iter {i + 1}/{args.pr_iters}: {t_it:.1f}s "
              f"(zero={t_zero:.1f} scatter={t_scat:.1f} combine={t_comb:.1f})",
              flush=True)
    with runner.driver.session(database=runner.database) as s:
        top = [dict(r) for r in s.run(PR_TOP)]
    t, _ = run(PR_CLEANUP); raw.writerow(["pr_cleanup", rnd, "", f"{t:.3f}", "", ""])
    return {
        "nperson": nperson,
        "iters": args.pr_iters,
        "damping": DAMPING,
        "per_iter_s": iters,
        "total_s": sum(iters),
        "top3": top,
    }


def phase_wcc(runner, args, rnd, raw):
    print(f"[wcc] round {rnd}: max_iters={args.wcc_max_iters}", flush=True)

    def run(q):
        t0 = time.perf_counter()
        with runner.driver.session(database=runner.database) as s:
            summary = s.run(q, timeout_=datetime.timedelta(
                seconds=runner.query_timeout_s)).consume()
        return time.perf_counter() - t0, summary.counters.properties_set

    t, _ = run(WCC_INIT); raw.writerow(["wcc_init", rnd, "", f"{t:.3f}", "", ""])
    iters, total_set = [], 0
    for i in range(args.wcc_max_iters):
        t_it, nset = run(WCC_ITER)
        iters.append(t_it)
        total_set += nset
        raw.writerow(["wcc_iter", rnd, i, f"{t_it:.3f}", nset, ""])
        print(f"  iter {i + 1}: {t_it:.1f}s updates={nset}", flush=True)
        if nset == 0:
            break
    with runner.driver.session(database=runner.database) as s:
        ncomp = s.run(WCC_COUNT).single()["ncomp"]
    t, _ = run(WCC_CLEANUP); raw.writerow(["wcc_cleanup", rnd, "", f"{t:.3f}", "", ""])
    return {
        "iters_run": len(iters),
        "converged": len(iters) < args.wcc_max_iters,
        "per_iter_s": iters,
        "total_s": sum(iters),
        "components": ncomp,
    }


def main():
    ap = argparse.ArgumentParser(description="v2 图算法基准：BFS/PageRank/WCC 纯 Cypher（PLAN §4.4 方案 B）")
    ap.add_argument("--uri", default="neo4j://localhost:7687")
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password", default="neo4j")
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--out-prefix", default="graphalgos_result")
    ap.add_argument("--rounds", type=int, default=1,
                    help="重复轮数（2026-10-24 决策：先 1，全部实验后补 2/3）")
    ap.add_argument("--bfs-seeds", type=int, default=50)
    ap.add_argument("--bfs-depth", type=int, default=3)
    ap.add_argument("--path-cap", type=int, default=1000000,
                    help="BFS 计数上限保护（WITH DISTINCT ... LIMIT $cap）")
    ap.add_argument("--pr-iters", type=int, default=20)
    ap.add_argument("--wcc-max-iters", type=int, default=50)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--sample-scan", type=int, default=2000)
    ap.add_argument("--sample-cache", default=".")
    ap.add_argument("--params-file", default=None,
                    help="freeze_params.py 冻结参数集 JSON（配对协议）；指定后跳过采样")
    ap.add_argument("--resample", action="store_true")
    args = ap.parse_args()

    out_dir = os.path.dirname(args.out_prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    runner = Runner(args.uri, args.user, args.password, args.database,
                    query_timeout_s=args.timeout)
    bfs_query = BFS_QUERY_TPL.replace("$depth", str(args.bfs_depth))
    summary = {
        "uri": args.uri,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rounds": args.rounds,
        "params": vars(args),
        "params_sha256": (hashlib.sha256(open(args.params_file, "rb").read()).hexdigest()
                          if getattr(args, "params_file", None) else None),
        "queries": {"bfs": bfs_query, "pr_scatter": PR_SCATTER,
                    "wcc_iter": WCC_ITER},
        "rounds_data": [],
    }
    try:
        seeds_data = sample_seeds(runner, args)
        seeds = seeds_data["bfs_seeds_sparse"] + seeds_data["bfs_seeds_dense"]
        nperson = runner.run_query("MATCH (p:Person) RETURN count(*) AS c")[0]["c"]
        print(f"[info] Person 总数 N={nperson}", flush=True)

        with open(args.out_prefix + "_graphalgos_raw.csv", "w", newline="") as f:
            raw = csv.writer(f)
            raw.writerow(["phase", "round", "seed_or_iter", "latency_s",
                          "effect", "error"])
            for rnd in range(1, args.rounds + 1):
                print(f"===== round {rnd}/{args.rounds} $(date) =====".replace(
                    "$(date)", time.strftime("%F %T")), flush=True)
                bfs = phase_bfs(runner, args, seeds, rnd, raw, bfs_query)
                pr = phase_pagerank(runner, args, nperson, rnd, raw)
                wcc = phase_wcc(runner, args, rnd, raw)
                summary["rounds_data"].append({"round": rnd, "bfs": bfs,
                                               "pagerank": pr, "wcc": wcc})
                print(f"[round {rnd}] bfs_total={bfs['lat'].get('total_s', 0):.1f}s "
                      f"pr_total={pr['total_s']:.1f}s wcc_total={wcc['total_s']:.1f}s "
                      f"wcc_comp={wcc['components']}", flush=True)
        with open(args.out_prefix + "_graphalgos.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[done] 汇总 → {args.out_prefix}_graphalgos.json", flush=True)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
