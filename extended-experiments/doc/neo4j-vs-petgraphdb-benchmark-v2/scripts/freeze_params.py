#!/usr/bin/env python3
"""
参数集冻结生成器（2026-10-25 审计后协议，见 AUDIT-AND-PROTOCOL.md §2.2）。

目的：为 SF10 三轮配对实验生成**一份**两侧共用、全部轮次共用的冻结参数集，
彻底消除"两系统独立采样"导致的参数混杂（rep1 实测 q5 参数重叠 0/240）。

输入（client 本地 datagen 产物，不连数据库、不占 server）：
  dynamic/person_knows_person_0_0.csv   → KNOWS 无向度（双侧库由同一 CSV 导入，
                                            逻辑图一致，故度数与可达性两侧通用）
  dynamic/post_0_0.csv                  → q5 Post 业务 id（按 id 排序 stride 取样，
                                            不再按内部 id 排序）
q1 的 lo/hi 与 q2 的 tag 列表直接沿用既有配对缓存（两侧实测一致，见审计 §1.2 #3）。

输出：
  <out>.json          全部参数集（业务 ID 为主）
  <out>.json.sha256   参数集 SHA-256（运行日志打印校验值）

确定性保证：全部选择由排序 + 位置决定（无 rand()），同一输入必得同一参数集。
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import deque

MICRO_SPARSE_N = 100
MICRO_DENSE_N = 100
Q4_N = 120
Q5_N = 120
Q2_N = 120
BFS_SPARSE_N = 25
BFS_DENSE_N = 25
IID_N = 100  # 内部 id 探针的业务 ID 池（Person；运行时映射为各系统 id(n)）


def sha256_file(path, _bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(_bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _knows_cols(header):
    """person_knows_person 表头为 'Person.id|Person.id|creationDate'：取前两列。"""
    idx = [i for i, h in enumerate(header) if h == "Person.id"]
    if len(idx) < 2:
        raise ValueError(f"person_knows 表头无法解析两列 Person.id: {header}")
    return idx[0], idx[1]


def load_knows_degrees(path):
    """person_knows_person CSV → {person_id: undirected_degree}。无向：两端都计数。"""
    deg = {}
    with open(path, newline="") as f:
        r = csv.reader(f, delimiter="|")
        ia, ib = _knows_cols(next(r))
        for row in r:
            a, b = int(row[ia]), int(row[ib])
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
    return deg


def pick_pools(deg):
    """确定性分池：按 (degree, id) 升序；稀疏=deg>=1 前 N，稠密=后 N（再按 degree 降序输出）。"""
    items = sorted(deg.items(), key=lambda kv: (kv[1], kv[0]))
    sparse = [pid for pid, d in items if d >= 1][:MICRO_SPARSE_N]
    dense = [pid for pid, d in items[-MICRO_DENSE_N:]][::-1]
    return sparse, dense


def build_adjacency(path):
    adj = {}
    with open(path, newline="") as f:
        r = csv.reader(f, delimiter="|")
        ia, ib = _knows_cols(next(r))
        for row in r:
            a, b = int(row[ia]), int(row[ib])
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return adj


def sp_partner(adj, src, max_hop=3):
    """确定性搭档：BFS 到 max_hop，取距离恰好 >=2 的最小 id 节点（!= src）。
    保证限深内可达；两侧逻辑图一致故两侧同可达。"""
    seen = {src}
    frontier = {src}
    for _ in range(max_hop):
        nxt = set()
        for u in frontier:
            nxt |= adj.get(u, ())
        nxt -= seen
        if not nxt:
            return None
        seen |= nxt
        cands = sorted(n for n in nxt if n != src)
        if cands and _ >= 1:  # 距离 >=2
            return cands[0]
        frontier = nxt
    return None


def stride_pick(sorted_items, limit):
    n = len(sorted_items)
    if n <= limit:
        return list(sorted_items)
    return [sorted_items[int(i * n / limit)] for i in range(limit)]


def load_post_ids(path):
    ids = []
    with open(path, newline="") as f:
        r = csv.reader(f, delimiter="|")
        header = next(r)
        i = header.index("id")
        for row in r:
            ids.append(int(row[i]))
    ids.sort()
    return ids


def main():
    ap = argparse.ArgumentParser(description="冻结 SF10 配对实验参数集（AUDIT-AND-PROTOCOL §2.2）")
    ap.add_argument("--datagen-dir", required=True, help="datagen-out-sf10/social_network 目录")
    ap.add_argument("--q1-lo", type=int, default=1262415954810, help="沿用既有配对缓存值")
    ap.add_argument("--q1-hi", type=int, default=1347528956866, help="沿用既有配对缓存值")
    ap.add_argument("--q2-tags-file", default=None,
                    help="含 q2_tags 列表的 JSON（沿用既有配对结果）；缺省用 --q2-tags-csv")
    ap.add_argument("--q2-tags-csv", default=None, help="static/tag_0_0.csv（按 name 排序 stride）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    d = args.datagen_dir
    knows = os.path.join(d, "dynamic", "person_knows_person_0_0.csv")
    posts = os.path.join(d, "dynamic", "post_0_0.csv")
    src_hashes = {
        "person_knows_person_0_0.csv": sha256_file(knows),
        "post_0_0.csv": sha256_file(posts),
    }

    t0 = time.time()
    deg = load_knows_degrees(knows)
    sparse, dense = pick_pools(deg)
    print(f"[pool] persons_with_knows={len(deg)} sparse={len(sparse)} dense={len(dense)} "
          f"({time.time() - t0:.1f}s)", flush=True)

    adj = build_adjacency(knows)
    sp_pairs = {}
    for tier, pool in (("sparse", sparse), ("dense", dense)):
        pairs, skipped = [], 0
        for pid in pool:
            partner = sp_partner(adj, pid)
            if partner is None:
                skipped += 1
            else:
                pairs.append({"ida": pid, "idb": partner})
        sp_pairs[tier] = pairs
        print(f"[sp] {tier}: pairs={len(pairs)} skipped={skipped}", flush=True)

    post_ids = load_post_ids(posts)
    q5 = stride_pick(post_ids, Q5_N)
    print(f"[q5] posts_total={len(post_ids)} sampled={len(q5)}", flush=True)

    if args.q2_tags_file:
        with open(args.q2_tags_file) as f:
            q2 = json.load(f)
        if isinstance(q2, dict):
            q2 = q2["q2_tags"]
        q2_src = args.q2_tags_file
    else:
        tagcsv = args.q2_tags_csv or os.path.join(d, "static", "tag_0_0.csv")
        with open(tagcsv, newline="") as f:
            r = csv.reader(f, delimiter="|")
            header = next(r)
            i = header.index("name")
            tags = sorted(row[i] for row in r)
        q2 = stride_pick(tags, Q2_N)
        q2_src = tagcsv
        src_hashes[os.path.basename(tagcsv)] = sha256_file(tagcsv)
    print(f"[q2] tags={len(q2)} (src={q2_src})", flush=True)

    data = {
        "format": 2,
        "protocol": "AUDIT-AND-PROTOCOL.md §2.2 (paired, frozen)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_files_sha256": src_hashes,
        "pool_sizes": {"sparse": len(sparse), "dense": len(dense)},
        "person_sparse": sparse,
        "person_dense": dense,
        "iid_pool_business_ids": sparse[:IID_N // 2] + dense[:IID_N // 2],
        "sp_pairs_sparse": sp_pairs["sparse"],
        "sp_pairs_dense": sp_pairs["dense"],
        "bfs_seeds": sparse[:BFS_SPARSE_N] + dense[:BFS_DENSE_N],
        "q1": {"lo": args.q1_lo, "hi": args.q1_hi},
        "q2_tags": q2,
        "q4_person_sparse": sparse[:Q4_N],
        "q5_post_ids": q5,
    }
    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)
    digest = sha256_file(args.out)
    with open(args.out + ".sha256", "w") as f:
        f.write(f"{digest}  {os.path.basename(args.out)}\n")
    print(f"[done] {args.out}\n[digest] sha256={digest}", flush=True)


if __name__ == "__main__":
    main()
