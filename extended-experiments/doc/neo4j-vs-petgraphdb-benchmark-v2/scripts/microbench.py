#!/usr/bin/env python3
"""
微基准 driver（v2）：LDBC SNB 算子级探针，用于拆解 PETGraphDB 时态扩展的固定开销。

在 v1（../neo4j-vs-petgraphdb-benchmark/scripts/bench/driver.py）基础上改造：
- 不再跑官方 IC/IS 查询，改为算子级探针（PLAN.md §4.3），Cypher 文本两侧通用，
  不依赖 APOC/GDS；
- 参数不再来自 datagen substitution_parameters，而是**从被测库在线采样**
  （度数低/高的 Person 各取 >=100 个），采样结果缓存到本地 JSON 供多轮复用；
- 每探针 warmup >=20 次 + 采样 >=100 次（参数轮转），记录 mean/median/p95/min/max；
- 单查询超时默认 1800s（SF100 用），超时计入 timeouts 字段、不中断后续测量；
- --threads N 支持多线程并发跑同一探针（吞吐测量），默认 1。

探针清单（节点参数化的探针分稀疏/稠密两档；聚合/内部 id 点查与节点度数无关，
按 na 单档跑）：
  点查        point-by-id            MATCH (p:Person {id:$id})
              point-by-internal-id   MATCH (n) WHERE id(n)=$iid
  一度扩展    expand-1hop-out        (p)-[:KNOWS]->(p2)  及反向 expand-1hop-in
  二度扩展    expand-2hop            (p)-[:KNOWS]-()-[:KNOWS]-(p2)  带 DISTINCT 计数
  3-hop 受限  khop-3-limited         (p)-[:KNOWS*1..3]-(p2)  加 LIMIT $cap 保护
  最短路径    shortest-path          shortestPath((a)-[:KNOWS*..D]-(b))，深度限 7
  聚合        agg-person-ip-prefix   按 Person.locationIP 前缀 group by count
              agg-message-year       按 Message.creationDate 年份 group by count
  写          write-set-prop         自还原属性更新 SET p.browserUsed = p.browserUsed
              write-create-delete    事务内创建+删除临时节点 _MicroBenchTmp

输出（--out-prefix 为前缀）：
  <prefix>_<probe>_<tier>.json   每探针独立 JSON（统计 + 元信息 + 查询文本）
  <prefix>_summary.json          汇总 JSON
  <prefix>_raw.csv               逐次执行原始记录（含 warmup、超时标记、参数 JSON）
  microbench_samples_<uri>.json  参数采样缓存（--sample-cache 目录下）

配对协议（2026-10-25 审计后，AUDIT-AND-PROTOCOL.md §2.2）：
  --params-file 指定 freeze_params.py 生成的冻结参数集（两侧/全轮次共用一份，
  业务 ID 为主）时，跳过在线采样，直接按该参数集执行；
  point-by-internal-id 探针在参数文件模式下使用 iid_pool_business_ids，
  启动时一次性映射为各系统内部 id(n)（映射不计入被测耗时，仅探针自身计时）。
  原始 CSV 的 params_json 列记录每次执行的参数，支持跨系统逐参数配对核对。
"""

import argparse
import csv
import datetime
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import threading
import time

import neo4j
from neo4j import GraphDatabase

logging.getLogger("neo4j").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# 探针定义
# ---------------------------------------------------------------------------
# tiered=True 的探针按稀疏/稠密两档各跑一遍（参数来自对应度数池）；
# tiered=False 的探针与节点度数无关，只跑 na 单档。
# 查询文本中的 $cap / $max_depth 等在 build_probes() 里按 CLI 参数实例化。

PROBES = [
    {
        "name": "point-by-id",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id}) "
                  "RETURN p.id AS prop_id, p.browserUsed AS browser_used",
    },
    {
        "name": "point-by-internal-id",
        "tiered": False,
        "cypher": "MATCH (n) WHERE id(n) = $iid "
                  "RETURN n.id AS prop_id, n.browserUsed AS browser_used",
    },
    {
        "name": "expand-1hop-out",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id})-[:KNOWS]->(p2) RETURN count(p2) AS c",
    },
    {
        "name": "expand-1hop-in",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id})<-[:KNOWS]-(p2) RETURN count(p2) AS c",
    },
    {
        "name": "expand-2hop",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id})-[:KNOWS]-()-[:KNOWS]-(p2) "
                  "RETURN count(DISTINCT p2) AS c",
    },
    {
        # 变长路径 + WITH DISTINCT ... LIMIT 保护：内核管道会把 LIMIT 下推到
        # 惰性展开，达到计数上限即短路（同一查询文本两侧通用）。
        "name": "khop-3-limited",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id})-[:KNOWS*1..3]-(p2) "
                  "WITH DISTINCT p2 LIMIT $cap RETURN count(*) AS c",
    },
    {
        "name": "shortest-path",
        "tiered": True,
        # $max_depth 由 CLI 注入（默认 7，防度数高节点路径爆炸）
        "cypher": "MATCH (a:Person {id: $ida}), (b:Person {id: $idb}) "
                  "MATCH path = shortestPath((a)-[:KNOWS*..$max_depth]-(b)) "
                  "RETURN length(path) AS len",
    },
    {
        "name": "agg-person-ip-prefix",
        "tiered": False,
        "cypher": "MATCH (p:Person) "
                  "RETURN substring(p.locationIP, 0, $prefix_len) AS g, count(*) AS c "
                  "ORDER BY c DESC",
    },
    {
        "name": "agg-message-year",
        "tiered": False,
        "cypher": "MATCH (m:Message) "
                  "RETURN datetime({epochMillis: m.creationDate}).year AS y, count(*) AS c "
                  "ORDER BY y",
    },
    {
        # 自还原式属性更新：走完整事务写路径（WAL、锁）但不改数据
        "name": "write-set-prop",
        "tiered": True,
        "cypher": "MATCH (p:Person {id: $id}) "
                  "SET p.browserUsed = p.browserUsed RETURN count(p) AS c",
    },
    {
        # 创建+删除临时节点：单条语句 = 单事务，原子完成，不留残留数据
        "name": "write-create-delete",
        "tiered": True,
        "cypher": "CREATE (n:_MicroBenchTmp {id: $id, ts: $ts}) "
                  "WITH n DELETE n RETURN count(*) AS c",
    },
]


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def summarize(lat):
    s = sorted(lat)
    return {
        "count": len(lat),
        "mean_ms": statistics.mean(lat) * 1000,
        "median_ms": statistics.median(lat) * 1000,
        "p90_ms": percentile(s, 90) * 1000,
        "p95_ms": percentile(s, 95) * 1000,
        "p99_ms": percentile(s, 99) * 1000,
        "min_ms": s[0] * 1000,
        "max_ms": s[-1] * 1000,
    }


class Runner:
    """对 v1 Runner 的扩展：单查询超时（server 端 tx timeout + 客户端兜底判超时）、
    逐次记录带线程号与 timed_out 标记。"""

    def __init__(self, uri, user, password, database="neo4j",
                 query_timeout_s=1800, max_conn=64):
        self.driver = GraphDatabase.driver(
            uri, auth=(user, password),
            max_connection_pool_size=max(64, max_conn))
        self.database = database
        self.query_timeout_s = query_timeout_s
        self.lock = threading.Lock()
        # (probe, tag, thread, start_epoch, latency_s, nrecords, error, timed_out)
        self.rows = []

    def execute(self, pname, query, params, tag="", tid=0, record_params=None):
        t0 = time.perf_counter()
        err = ""
        timed_out = False
        nrec = 0
        try:
            with self.driver.session(database=self.database) as s:
                try:
                    # neo4j python driver 6.x：timeout_ 映射 Bolt tx_timeout，
                    # 由服务端在时限后中止查询并回 FAILURE（PETGraphDB 同为 4.4
                    # 内核 Bolt，语义一致）；旧版 driver 不支持该参数则回退。
                    res = s.run(query, params,
                                timeout_=datetime.timedelta(seconds=self.query_timeout_s))
                except TypeError:
                    res = s.run(query, params)
                nrec = len(list(res))
        except Exception as e:  # noqa
            err = str(e).replace("\n", " ")[:200]
            timed_out = "timeout" in err.lower() or "timed out" in err.lower()
        dt = time.perf_counter() - t0
        # 客户端兜底：无 server 端超时支持时按耗时判定
        if dt > self.query_timeout_s:
            timed_out = True
        with self.lock:
            pj = (json.dumps(params, sort_keys=True)
                  if record_params is not None else "")
            self.rows.append((pname, tag, tid, t0, dt, nrec, err, int(timed_out),
                              pj))
        return dt, err, timed_out

    def run_query(self, query, params=None):
        """采样阶段用的同步执行（不受 query_timeout 限制，一次性离线操作）。"""
        with self.driver.session(database=self.database) as s:
            return list(s.run(query, params or {}))

    def close(self):
        self.driver.close()

    def dump(self, out_csv):
        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["probe", "tag", "thread", "start_epoch", "latency_s",
                        "nrecords", "error", "timed_out", "params_json"])
            for r in self.rows:
                w.writerow(r)


# ---------------------------------------------------------------------------
# 参数在线采样（v2 改造核心：不再读 datagen substitution_parameters）
# ---------------------------------------------------------------------------

def _cache_file(args):
    """采样缓存路径：--sample-cache 为目录，文件名按 uri 区分（两系统各自采样）。"""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", args.uri).strip("_") or "default"
    path = args.sample_cache
    if path.endswith(".json"):
        return path
    return os.path.join(path, f"microbench_samples_{safe}.json")


def sample_params(runner, args):
    """从被测库在线采样各探针参数，缓存到本地 JSON 供多轮复用。

    --params-file（freeze_params.py 输出）存在时**跳过采样**，直接使用冻结的
    共享参数集（两侧/全轮次同一份，业务 ID 配对）；内部 id 池则现场把业务 ID
    一次性映射为本系统内部 id(n)（映射不计入被测查询计时）。

    采样策略（一次性操作，SF100 机械盘上也就跑一遍，之后走缓存）：
    1. 随机抽 --sample-scan 个 Person，算其 KNOWS 无向度；
    2. 度数升序取前 N 个（deg >= 1）为稀疏池，降序取前 N 个为稠密池
       （N = --param-limit，默认 100）；
    3. 内部 id 池：全图随机抽 N 个 id(n)（内部 id 与度数无关，仅 na 档用）；
    4. 最短路径参数对：对每个池内 Person，在 *2..3 跳邻域内取 1 个邻居
       （WITH b LIMIT 1 惰性短路防稠密节点爆炸），保证参数对在限深内必可达。
    """
    if getattr(args, "params_file", None):
        with open(args.params_file) as f:
            frozen = json.load(f)
        # 内部 id 池：业务 ID → 本系统内部 id（一次性映射，不计入被测计时）
        bids = frozen.get("iid_pool_business_ids", [])
        iids = [r["iid"] for r in runner.run_query(
            "UNWIND $bids AS bid MATCH (p:Person {id: bid}) RETURN id(p) AS iid",
            {"bids": bids})] if bids else []
        print(f"[params] 冻结参数集 {args.params_file}（format={frozen.get('format')}），"
              f"内部 id 映射 {len(iids)}/{len(bids)}", flush=True)
        return {
            "format": frozen.get("format"),
            "uri": args.uri,
            "person_sparse": frozen["person_sparse"],
            "person_dense": frozen["person_dense"],
            "internal_ids": iids,
            "sp_pairs_sparse": frozen["sp_pairs_sparse"],
            "sp_pairs_dense": frozen["sp_pairs_dense"],
        }

    cache = _cache_file(args)
    if os.path.exists(cache) and not args.resample:
        print(f"[sample] 使用缓存 {cache}", flush=True)
        with open(cache) as f:
            return json.load(f)

    print(f"[sample] 在线采样参数（scan={args.sample_scan}, limit={args.param_limit}）...",
          flush=True)
    t0 = time.time()

    # 1+2) 随机 Person + 度数，分稀疏/稠密池
    rows = runner.run_query(
        "MATCH (p:Person) WITH p ORDER BY rand() LIMIT $scan "
        "OPTIONAL MATCH (p)-[:KNOWS]-(p2) "
        "WITH p, count(p2) AS deg RETURN p.id AS id, deg ORDER BY deg",
        {"scan": args.sample_scan})
    persons = [(r["id"], r["deg"]) for r in rows]
    persons.sort(key=lambda x: x[1])
    lim = args.param_limit
    sparse = [pid for pid, deg in persons if deg >= 1][:lim]
    dense = [pid for pid, deg in persons[-lim:]]
    if len(sparse) < lim or len(dense) < lim:
        print(f"[warn] 度数池不足 lim={lim}（sparse={len(sparse)}, dense={len(dense)}），"
              f"可增大 --sample-scan", flush=True)

    # 3) 内部 id 池（na 档探针用）
    iids = [r["iid"] for r in runner.run_query(
        "MATCH (n) WITH n ORDER BY rand() LIMIT $lim RETURN id(n) AS iid",
        {"lim": lim})]

    # 4) 最短路径参数对：2..3 跳邻域内取一个搭档（保证限深内可达）
    def sp_pairs(pool):
        pairs = []
        for pid in pool:
            recs = runner.run_query(
                "MATCH (a:Person {id: $aid}) MATCH (a)-[:KNOWS*2..3]-(b:Person) "
                "WITH b LIMIT 1 RETURN b.id AS bid", {"aid": pid})
            if recs and recs[0]["bid"] is not None:
                pairs.append({"ida": pid, "idb": recs[0]["bid"]})
        return pairs

    data = {
        "format": 1,
        "uri": args.uri,
        "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "person_sparse": sparse,
        "person_dense": dense,
        "internal_ids": iids,
        "sp_pairs_sparse": sp_pairs(sparse),
        "sp_pairs_dense": sp_pairs(dense),
    }
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    with open(cache, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[sample] 完成，耗时 {time.time() - t0:.1f}s → {cache}", flush=True)
    return data


# ---------------------------------------------------------------------------
# 探针参数组装
# ---------------------------------------------------------------------------

def build_probes(args, samples):
    """返回 [(probe_name, tier, query_text, [params...]), ...]（已展开稀疏/稠密档）。"""
    tier_pools = {"sparse": samples["person_sparse"], "dense": samples["person_dense"]}
    out = []
    for spec in PROBES:
        name = spec["name"]
        query = spec["cypher"]
        if name == "shortest-path":
            query = query.replace("$max_depth", str(int(args.sp_max_depth)))
        tiers = ["sparse", "dense"] if spec["tiered"] else ["na"]
        for tier in tiers:
            if name == "agg-person-ip-prefix":
                params = [{"prefix_len": int(args.ip_prefix_len)}]
            elif name == "agg-message-year":
                params = [{}]
            elif name == "point-by-internal-id":
                params = [{"iid": v} for v in samples["internal_ids"]]
                if not params:
                    print(f"[warn] 无内部 id 参数，跳过 {name}", flush=True)
                    continue
            elif name == "shortest-path":
                params = [dict(p) for p in samples[f"sp_pairs_{tier}"]]
                if not params:
                    print(f"[warn] 无最短路径参数对，跳过 {name}.{tier}", flush=True)
                    continue
            else:
                pool = tier_pools[tier]
                if not pool:
                    print(f"[warn] 空参数池，跳过 {name}.{tier}", flush=True)
                    continue
                if name == "khop-3-limited":
                    params = [{"id": pid, "cap": int(args.path_cap)} for pid in pool]
                elif name == "write-create-delete":
                    # ts 每次执行时注入（见 run_probes），参数池只放 id
                    params = [{"id": pid} for pid in pool]
                else:
                    params = [{"id": pid} for pid in pool]
            out.append((name, tier, query, params))
    return out


# ---------------------------------------------------------------------------
# 测量协议：每探针 warmup >=20 + 采样 >=100（参数轮转），支持多线程吞吐
# ---------------------------------------------------------------------------

def run_probes(runner, probes, warmup, reps, threads, out_prefix):
    stats = {}
    for pname, tier, query, params in probes:
        tag = f"{pname}.{tier}"
        total = warmup + reps
        lock = threading.Lock()
        results = []      # (i, dt, err, timed_out)，含 warmup，供原始统计
        warmup_done = {}  # tid -> 该线程最后一个 warmup 迭代的完成时刻

        def work(tid):
            local = []
            my_warmup_idx = [i for i in range(warmup) if i % threads == tid]
            last_warmup = max(my_warmup_idx) if my_warmup_idx else -1
            for i in range(tid, total, threads):
                p = params[i % len(params)]
                if pname == "write-create-delete":
                    p = dict(p, ts=time.time_ns())  # 每次不同的临时节点属性
                dt, err, to = runner.execute(pname, query, p, tag, tid,
                                             record_params=p)
                local.append((i, dt, err, to))
                if i == last_warmup:
                    warmup_done[tid] = time.time()
            with lock:
                results.extend(local)

        t_start = time.time()
        ths = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        wall_s = time.time() - t_start

        lat = [dt for (i, dt, err, to) in results if i >= warmup and not err]
        n_err = sum(1 for (_, _, err, _) in results if err)
        n_to = sum(1 for (_, _, _, to) in results if to)
        # 非超时错误打印前 3 条（超时是 SF100 预期内事件，由 timeouts 字段汇总）
        shown = 0
        for (_, _, err, to) in results:
            if err and not to and shown < 3:
                print(f"[err] {tag}: {err}", flush=True)
                shown += 1
        # 测量阶段墙钟：所有线程最后一个 warmup 迭代完成 → 全部 join
        if threads > 1 and warmup > 0 and len(warmup_done) == threads:
            measure_wall = wall_s - (max(warmup_done.values()) - t_start)
        else:
            measure_wall = wall_s
        measure_wall = max(measure_wall, 1e-9)

        st = summarize(lat) if lat else {"count": 0}
        st.update({
            "probe": pname,
            "tier": tier,
            "query": query,
            "n_params": len(params),
            "warmup": warmup,
            "reps": reps,
            "threads": threads,
            "errors": n_err,
            "timeouts": n_to,
            "wall_s": wall_s,
            "measure_wall_s": measure_wall,
            "throughput_qps": len(lat) / measure_wall,
        })
        stats[tag] = st
        if lat:
            print(f"{tag:34s} n={st['count']:4d} mean={st['mean_ms']:10.2f}ms "
                  f"median={st['median_ms']:10.2f}ms p95={st['p95_ms']:10.2f}ms "
                  f"min={st['min_ms']:9.2f}ms max={st['max_ms']:10.2f}ms "
                  f"to={n_to}", flush=True)
        else:
            print(f"{tag:34s} ALL {n_err} EXECUTIONS FAILED (timeouts={n_to})",
                  flush=True)
        with open(f"{out_prefix}_{pname}_{tier}.json", "w") as f:
            json.dump(st, f, indent=2)
    return stats


def main():
    ap = argparse.ArgumentParser(description="v2 微基准：SNB 算子级探针（PLAN §4.3）")
    ap.add_argument("--uri", default="neo4j://localhost:7687",
                    help="Bolt 连接串，bolt://host:port 或 neo4j://host:port")
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password", default="neo4j")
    ap.add_argument("--database", default="neo4j")
    ap.add_argument("--out-prefix", default="microbench_result")
    ap.add_argument("--probes", default="all",
                    help="探针清单：'all' 或逗号分隔的探针名")
    ap.add_argument("--warmup", type=int, default=20, help="每探针预热次数（协议 >=20）")
    ap.add_argument("--reps", type=int, default=100, help="每探针采样次数（协议 >=100）")
    ap.add_argument("--param-limit", type=int, default=100,
                    help="每档参数个数（度数池/内部 id/参数对），协议 >=100")
    ap.add_argument("--sample-scan", type=int, default=2000,
                    help="度数采样时随机扫描的 Person 数（越大度数池越准）")
    ap.add_argument("--threads", type=int, default=1,
                    help="并发线程数（=1 纯延迟测量；>1 兼测吞吐）")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="单查询超时秒数（SF100 用），超时计入 timeouts 不中断")
    ap.add_argument("--path-cap", type=int, default=100000,
                    help="khop-3-limited 的计数上限保护（WITH DISTINCT ... LIMIT $cap）")
    ap.add_argument("--sp-max-depth", type=int, default=7,
                    help="shortest-path 深度上限（5-7 防爆炸）")
    ap.add_argument("--ip-prefix-len", type=int, default=8,
                    help="agg-person-ip-prefix 的 locationIP 前缀长度")
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

    # 探针筛选
    if args.probes != "all":
        wanted = {s.strip() for s in args.probes.split(",")}
        for w in wanted:
            if w not in {p["name"] for p in PROBES}:
                print(f"[warn] 未知探针名: {w}", file=sys.stderr)
        probe_specs = [p for p in PROBES if p["name"] in wanted]
    else:
        probe_specs = PROBES

    runner = Runner(args.uri, args.user, args.password, args.database,
                    query_timeout_s=args.timeout, max_conn=args.threads * 2)
    try:
        samples = sample_params(runner, args)
        # build_probes 用的是全量 PROBES 定义，这里按 --probes 过滤展开结果
        probes = [pb for pb in build_probes(args, samples)
                  if pb[0] in {p["name"] for p in probe_specs}]
        stats = run_probes(runner, probes, args.warmup, args.reps,
                           args.threads, args.out_prefix)
        summary = {
            "uri": args.uri,
            "database": args.database,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "warmup": args.warmup,
            "reps": args.reps,
            "threads": args.threads,
            "timeout_s": args.timeout,
            "param_limit": args.param_limit,
            "sample_cache": _cache_file(args),
            "params_file": getattr(args, "params_file", None),
            "params_sha256": (hashlib.sha256(open(args.params_file, "rb").read()).hexdigest()
                              if getattr(args, "params_file", None) else None),
            "probes": stats,
        }
        with open(args.out_prefix + "_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(f"[done] 汇总 → {args.out_prefix}_summary.json", flush=True)
    finally:
        runner.dump(args.out_prefix + "_raw.csv")
        runner.close()


if __name__ == "__main__":
    main()
