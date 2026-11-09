#!/usr/bin/env python3
"""
自设分析查询组基准（v2，PLAN §4.5）：BI 放弃后覆盖"重聚合/分析型"访问模式。

设计原则（PLAN §4.5）：
- 纯 Cypher 4.4，无 APOC/GDS，两侧（Neo4j / PETGraphDB）执行完全相同的查询文本；
- 非官方查询，论文中明确标注为自定义 workload（与官方 Interactive 区分）；
- 5 个查询覆盖不同算子组合：大 group by + 时间窗 / 多对多关联 + TopK / 聚合分布 /
  受限多跳 count / 路径深度聚合。

查询清单（Cypher 文本逐字冻结，两侧通用）：
  q1-country-year-window   Country×Year 统计 Message 量（大 group by + 时间窗参数）
  q2-tag-monthly-topk      Tag 的月度讨论热度 TopK（多对多 + 聚合 + LIMIT 12）
  q3-degree-distribution   人际网络 KNOWS 度数分布（聚合 + 分布，无参数）
  q4-influence-3hop        3 跳影响力传播计数（逐跳 collect DISTINCT 防路径爆炸）
  q5-thread-depth          讨论线程 REPLY_OF 深度分布（无回复的 Post 返回 0 行，正常）

参数在线采样（一次性，结果缓存 JSON 多轮复用）：
  q1  Message.creationDate 全局 min/max（全扫一次），[lo,hi) 内确定性均匀取起点
      （sha256(i) 而非 rand，保证可复现），窗宽 30/90/365 天 × 40 起点 = 120 组；
  q2  全部 Tag 按 id(t) 排序后 stride 均匀取 120 个（不足则全取 + warn）；
  q4  仿 microbench 度数池：随机扫 --sample-scan 个 Person 算 KNOWS 无向度，
      升序取 deg>=1 的前 120 个稀疏池（稠密种子 3 跳会爆，只用稀疏池）；
  q5  全部 Post 按 id(p) 排序后 stride 均匀取 120 个（不足则全取 + warn）。

协议（对齐 microbench 与 2026-10-24 "先 rep1" 决策）：
  每查询 warmup 次 + reps 次正式采样（参数轮转 i % len(params)）；
  单查询超时计入 timeouts 不中断；逐次记录进 raw CSV（probe 名 q1..q5）。

输出（--out-prefix 为前缀）：
  <prefix>_<qname>.json   每查询统计（含查询文本 + 参数个数 + errors/timeouts）
  <prefix>_summary.json   汇总 JSON
  <prefix>_raw.csv        逐次执行原始记录（复用 Runner.dump 的记录格式）
  analysis_samples_<uri>.json  参数采样缓存（--sample-cache 目录下，按 uri 分文件）
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from microbench import Runner, summarize  # 复用：driver 连接 / 单查询超时 / 原始记录

DAY_MS = 24 * 3600 * 1000  # epoch millis per day

# ---------------------------------------------------------------------------
# 查询定义（Cypher 文本逐字冻结；参数名 $start/$end/$tag/$id）
# ---------------------------------------------------------------------------

QUERIES = [
    {
        "name": "q1-country-year-window",
        # SNB schema：Message(Post) 的 IS_LOCATED_IN 直指 Country（Comment 无此边），
        # 不能走 City→IS_PART_OF（2026-10-25 实测 0 行，官方查询 complex-11 佐证）。
        "cypher": "MATCH (m:Message) WHERE m.creationDate >= $start AND m.creationDate < $end "
                  "MATCH (m)-[:IS_LOCATED_IN]->(co:Country) "
                  "RETURN co.name AS country, datetime({epochMillis: m.creationDate}).year AS y, "
                  "count(*) AS c ORDER BY c DESC",
    },
    {
        "name": "q2-tag-monthly-topk",
        "cypher": "MATCH (m:Message)-[:HAS_TAG]->(t:Tag {name: $tag}) "
                  "RETURN datetime({epochMillis: m.creationDate}).year * 100 + "
                  "datetime({epochMillis: m.creationDate}).month AS ym, count(*) AS c "
                  "ORDER BY c DESC LIMIT 12",
    },
    {
        "name": "q3-degree-distribution",
        "cypher": "MATCH (p:Person) RETURN size((p)-[:KNOWS]-()) AS d, count(*) AS c ORDER BY d",
    },
    {
        "name": "q4-influence-3hop",
        "cypher": "MATCH (p:Person {id: $id})-[:KNOWS]-(a) WITH p, collect(DISTINCT a) AS l1 "
                  "UNWIND l1 AS a MATCH (a)-[:KNOWS]-(b) WHERE b <> p "
                  "WITH p, l1, collect(DISTINCT b) AS l2raw "
                  "WITH p, l1, l1 + [x IN l2raw WHERE NOT x IN l1] AS l2 "
                  "UNWIND l2 AS b MATCH (b)-[:KNOWS]-(c) "
                  "WITH l1, l2, collect(DISTINCT c) AS l3raw "
                  "WITH l1, l2, [x IN l3raw WHERE NOT x IN l1 AND NOT x IN l2] AS l3 "
                  "RETURN size(l1) + size(l2) + size(l3) AS reach",
    },
    {
        "name": "q5-thread-depth",
        "cypher": "MATCH path=(root:Post {id: $id})<-[:REPLY_OF*]-(c:Comment) "
                  "RETURN length(path) AS d, count(*) AS c ORDER BY d",
    },
]

# q1 时间窗宽档（epoch millis）：30 天 / 90 天 / 365 天，每档起点数
Q1_WINDOWS_MS = [30 * DAY_MS, 90 * DAY_MS, 365 * DAY_MS]
Q1_STARTS_PER_WINDOW = 40  # 3 档 × 40 = 120 组参数


# ---------------------------------------------------------------------------
# 参数在线采样（缓存 analysis_samples_<uri>.json，命中即跳过）
# ---------------------------------------------------------------------------

def _cache_file(args):
    """采样缓存路径：--sample-cache 为目录，文件名按 uri 区分（两系统各自采样）。"""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", args.uri).strip("_") or "default"
    path = args.sample_cache
    if path.endswith(".json"):
        return path
    return os.path.join(path, f"analysis_samples_{safe}.json")


def _stride_pick(items, limit):
    """按 stride 均匀取 limit 个（items 已按序）；不足 limit 则全取并 warn。"""
    n = len(items)
    if n <= limit:
        if n < limit:
            print(f"[warn] 候选仅 {n} 个（< limit={limit}），全部取用", flush=True)
        return list(items)
    return [items[int(i * n / limit)] for i in range(limit)]


def _det_hash(s):
    """进程间可复现的确定性 hash（不依赖 PYTHONHASHSEED）。"""
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def sample_params(runner, args):
    """q1 的 min/max、q2/q5 的 stride、q4 的度数池，全部在线采样后写入同一缓存。

    --params-file（freeze_params.py 输出）存在时跳过采样，直接用冻结共享参数集。"""
    if getattr(args, "params_file", None):
        with open(args.params_file) as f:
            frozen = json.load(f)
        print(f"[params] 冻结参数集 {args.params_file}（format={frozen.get('format')}）",
              flush=True)
        return {
            "format": frozen.get("format"),
            "uri": args.uri,
            "q1": frozen["q1"],
            "q2_tags": frozen["q2_tags"],
            "q4_person_sparse": frozen["q4_person_sparse"],
            "q5_post_ids": frozen["q5_post_ids"],
        }
    cache = _cache_file(args)
    if os.path.exists(cache) and not args.resample:
        print(f"[sample] 使用缓存 {cache}", flush=True)
        with open(cache) as f:
            return json.load(f)

    print(f"[sample] 在线采样参数（scan={args.sample_scan}, limit={args.param_limit}）...",
          flush=True)
    t0 = time.time()
    lim = args.param_limit

    # q1) Message.creationDate 全局 [lo, hi)（全扫一次，结果进缓存）
    rec = runner.run_query(
        "MATCH (m:Message) RETURN min(m.creationDate) AS lo, max(m.creationDate) AS hi")
    if not rec or rec[0]["lo"] is None:
        raise RuntimeError("q1 采样失败：库中无 Message.creationDate")
    lo, hi = int(rec[0]["lo"]), int(rec[0]["hi"])
    print(f"[sample] q1 creationDate 范围 [{lo}, {hi})", flush=True)

    # q2) 全部 Tag 按内部 id 排序，stride 均匀取样
    tags = [r["name"] for r in runner.run_query(
        "MATCH (t:Tag) RETURN t.name AS name ORDER BY id(t)")]
    tags = _stride_pick(tags, lim)

    # q4) 度数池：随机扫 --sample-scan 个 Person 算 KNOWS 无向度，
    #     升序取 deg>=1 的前 lim 个稀疏池（稠密种子 3 跳会爆，只用稀疏池）
    rows = runner.run_query(
        "MATCH (p:Person) WITH p ORDER BY rand() LIMIT $scan "
        "OPTIONAL MATCH (p)-[:KNOWS]-(p2) "
        "WITH p, count(p2) AS deg RETURN p.id AS id, deg ORDER BY deg",
        {"scan": args.sample_scan})
    persons = sorted(((r["id"], r["deg"]) for r in rows), key=lambda x: x[1])
    sparse = [pid for pid, deg in persons if deg >= 1][:lim]
    if len(sparse) < lim:
        print(f"[warn] q4 稀疏池不足 lim={lim}（实得 {len(sparse)}），"
              f"可增大 --sample-scan", flush=True)

    # q5) 全部 Post 按内部 id 排序，stride 均匀取样
    posts = [r["id"] for r in runner.run_query(
        "MATCH (p:Post) RETURN p.id AS id ORDER BY id(p)")]
    posts = _stride_pick(posts, lim)

    data = {
        "format": 1,
        "uri": args.uri,
        "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "q1": {"lo": lo, "hi": hi},
        "q2_tags": tags,
        "q4_person_sparse": sparse,
        "q5_post_ids": posts,
    }
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    with open(cache, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[sample] 完成，耗时 {time.time() - t0:.1f}s → {cache}", flush=True)
    return data


# ---------------------------------------------------------------------------
# 查询参数组装
# ---------------------------------------------------------------------------

def build_queries(args, samples):
    """返回 [(qname, query_text, [params...]), ...]。"""
    lim = args.param_limit
    out = []
    for spec in QUERIES:
        name, query = spec["name"], spec["cypher"]
        if name == "q1-country-year-window":
            lo, hi = samples["q1"]["lo"], samples["q1"]["hi"]
            params = []
            for w in Q1_WINDOWS_MS:
                width = min(w, hi - lo)  # 数据时间跨度小于窗宽时收敛到全域
                span = hi - width - lo
                for i in range(Q1_STARTS_PER_WINDOW):
                    # 确定性均匀起点：sha256("q1:{width}:{i}") / 2^256 ∈ [0,1)
                    frac = _det_hash(f"q1:{w}:{i}") / float(1 << 256)
                    start = lo + int(frac * span)
                    params.append({"start": start, "end": start + width})
        elif name == "q2-tag-monthly-topk":
            params = [{"tag": t} for t in samples["q2_tags"]]
        elif name == "q3-degree-distribution":
            params = [{}]
        elif name == "q4-influence-3hop":
            params = [{"id": pid} for pid in samples["q4_person_sparse"]]
        elif name == "q5-thread-depth":
            params = [{"id": pid} for pid in samples["q5_post_ids"]]
        if not params:
            print(f"[warn] 空参数集，跳过 {name}", flush=True)
            continue
        out.append((name, query, params))
    return out


# ---------------------------------------------------------------------------
# 测量协议：每查询 warmup + reps（参数轮转），单查询超时不中断
# ---------------------------------------------------------------------------

def run_queries(runner, queries, warmup, reps, out_prefix):
    stats = {}
    for qname, query, params in queries:
        total = warmup + reps
        t_start = time.time()
        results = []  # (i, dt, err, timed_out, nrec)，含 warmup
        for i in range(total):
            p = params[i % len(params)]
            dt, err, to = runner.execute(qname, query, p, tag=qname, tid=0,
                                         record_params=p)
            nrec = runner.rows[-1][5] if runner.rows else 0
            results.append((i, dt, err, to, nrec))
        wall_s = time.time() - t_start

        lat = [dt for (i, dt, err, _, _) in results if i >= warmup and not err]
        n_err = sum(1 for (_, _, err, _, _) in results if err)
        n_to = sum(1 for (_, _, _, to, _) in results if to)
        # q5 无回复的 Post 返回 0 行属正常，单独统计空结果不计错误
        n_empty = sum(1 for (i, _, err, _, nrec) in results
                      if i >= warmup and not err and nrec == 0)
        shown = 0
        for (_, _, err, to, _) in results:
            if err and not to and shown < 3:
                print(f"[err] {qname}: {err}", flush=True)
                shown += 1

        st = summarize(lat) if lat else {"count": 0}
        st.update({
            "query_name": qname,
            "query": query,
            "n_params": len(params),
            "warmup": warmup,
            "reps": reps,
            "errors": n_err,
            "timeouts": n_to,
            "empty_results": n_empty,
            "wall_s": wall_s,
        })
        stats[qname] = st
        if lat:
            print(f"{qname:26s} n={st['count']:4d} mean={st['mean_ms']:10.2f}ms "
                  f"median={st['median_ms']:10.2f}ms p95={st['p95_ms']:10.2f}ms "
                  f"min={st['min_ms']:9.2f}ms max={st['max_ms']:10.2f}ms "
                  f"to={n_to} empty={n_empty}", flush=True)
        else:
            print(f"{qname:26s} ALL {n_err} EXECUTIONS FAILED (timeouts={n_to})",
                  flush=True)
        with open(f"{out_prefix}_{qname}.json", "w") as f:
            json.dump(st, f, indent=2)
    return stats


def main():
    ap = argparse.ArgumentParser(description="v2 自设分析查询组（PLAN §4.5，替代 BI 的 OLAP 维度）")
    ap.add_argument("--uri", default="neo4j://localhost:7687",
                    help="Bolt 连接串，bolt://host:port 或 neo4j://host:port")
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password", default="neo4j")
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--out-prefix", default="analysis_result")
    ap.add_argument("--warmup", type=int, default=5, help="每查询预热次数")
    ap.add_argument("--reps", type=int, default=100, help="每查询正式采样次数")
    ap.add_argument("--param-limit", type=int, default=120,
                    help="每查询参数个数（q2/q4/q5 取样上限；q1 固定 120 = 3 窗宽 × 40）")
    ap.add_argument("--sample-scan", type=int, default=2000,
                    help="q4 度数采样时随机扫描的 Person 数")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="单查询超时秒数（SF100 用），超时计入 timeouts 不中断")
    ap.add_argument("--sample-cache", default=".",
                    help="采样缓存目录（或 .json 文件路径），默认当前目录；"
                         "文件名按 uri 区分")
    ap.add_argument("--params-file", default=None,
                    help="freeze_params.py 冻结参数集 JSON（配对协议）；"
                         "指定后跳过在线采样")
    ap.add_argument("--resample", action="store_true",
                    help="忽略已有缓存，强制重新在线采样")
    args = ap.parse_args()

    out_dir = os.path.dirname(args.out_prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    runner = Runner(args.uri, args.user, args.password, args.database,
                    query_timeout_s=args.timeout)
    try:
        samples = sample_params(runner, args)
        queries = build_queries(args, samples)
        stats = run_queries(runner, queries, args.warmup, args.reps, args.out_prefix)
        summary = {
            "uri": args.uri,
            "database": args.database,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "warmup": args.warmup,
            "reps": args.reps,
            "timeout_s": args.timeout,
            "param_limit": args.param_limit,
            "sample_cache": _cache_file(args),
            "workload": "custom analysis queries (PLAN §4.5, replaces SNB BI OLAP)",
            "queries": stats,
        }
        with open(args.out_prefix + "_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[done] 汇总 → {args.out_prefix}_summary.json", flush=True)
    finally:
        runner.dump(args.out_prefix + "_raw.csv")
        runner.close()


if __name__ == "__main__":
    main()
