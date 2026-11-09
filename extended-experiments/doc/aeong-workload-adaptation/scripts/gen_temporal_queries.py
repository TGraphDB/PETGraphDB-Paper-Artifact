#!/usr/bin/env python3
"""生成 PETGraphDB 版 T-mgBench Q1–Q4 冻结请求集（README §6-P2）。

复用 AeonG 生成器的 hot/warm/cold 顶点清单（peak_vertices/num*_update_count_*.csv，
CSV 格式 "id,count"）；修复 AeonG mix 模式的 cold 误用 bug（33/33/34）。
查询模板见 README §3.3；时间轴 t ∈ [time-min, time-max]，区间长 = interval。

输出: <out-dir>/q{1..4}_{mix,hot,warm,cold}.txt + manifest.json（SHA-256）。
确定性: 固定 seed。
"""
import argparse
import csv
import hashlib
import json
import os
import random


def read_ids(path):
    ids = []
    with open(path) as f:
        for row in csv.reader(f):
            if row:
                ids.append(row[0])
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peak-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--time-min", type=int, default=1)
    ap.add_argument("--time-max", type=int, default=320000)
    ap.add_argument("--interval", type=int, default=100)
    ap.add_argument("--per-type", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--intervals", default="",
                    help="逗号分隔区间长，生成 q4_mix_int<N>.txt 变体")
    args = ap.parse_args()

    prefix = None
    for name in os.listdir(args.peak_dir):
        if name.endswith("_update_count_hot.csv"):
            prefix = name[: -len("_update_count_hot.csv")]
            break
    assert prefix, "hot csv not found"

    def pool(kind):
        return read_ids(os.path.join(args.peak_dir, f"{prefix}_update_count_{kind}.csv"))

    hot, warm, cold = pool("hot"), pool("warm"), pool("cold")
    rng = random.Random(args.seed)

    def pick_vids(freq):
        if freq == "mix":
            return ([rng.choice(hot) for _ in range(34)]
                    + [rng.choice(warm) for _ in range(33)]
                    + [rng.choice(cold) for _ in range(33)])
        src = {"hot": hot, "warm": warm, "cold": cold}[freq]
        return [rng.choice(src) for _ in range(args.per_type)]

    def t_point():
        return rng.randint(args.time_min, args.time_max - args.interval)

    def t_pair():
        t1 = t_point()
        return t1, t1 + args.interval

    def q1(v, t):
        return (f"MATCH (r:User {{id:{v}}}) WHERE tp.getPoint(r, 'valid', {t}) = 1 "
                f"RETURN r.id AS id, tp.getPoint(r, 'completion_percentage', {t}) AS cp;")

    def q2(v, t1, t2):
        return (f"MATCH (r:User {{id:{v}}}) CALL tp.get(r, 'valid', {t1}, {t2}) YIELD value AS vv "
                f"WITH r, vv WHERE vv = 1 "
                f"CALL tp.get(r, 'completion_percentage', {t1}, {t2}) "
                f"YIELD timeBegin, timeEnd, value RETURN r.id AS id, timeBegin, timeEnd, value;")

    def q3(v, t):
        return (f"MATCH (n:User {{id:{v}}})-[e:Friend]->(r) "
                f"WHERE tp.getPoint(e, 'valid', {t}) = 1 AND tp.getPoint(r, 'valid', {t}) = 1 "
                f"RETURN n.id AS nid, r.id AS rid;")

    def q4(v, t1, t2):
        return (f"MATCH (n:User {{id:{v}}})-[e:Friend]->(r) "
                f"CALL tp.get(e, 'valid', {t1}, {t2}) YIELD value AS ev "
                f"WITH n, e, r, ev WHERE ev = 1 "
                f"CALL tp.get(r, 'valid', {t1}, {t2}) YIELD value AS rv "
                f"WITH n, e, r, rv WHERE rv = 1 "
                f"RETURN n.id AS nid, r.id AS rid;")

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = {"seed": args.seed, "time_min": args.time_min,
                "time_max": args.time_max, "interval": args.interval,
                "per_type": args.per_type, "files": {}}
    for freq in ("mix", "hot", "warm", "cold"):
        vids = pick_vids(freq)
        batches = {"q1": [], "q2": [], "q3": [], "q4": []}
        for v in vids:
            t = t_point()
            t1, t2 = t_pair()
            batches["q1"].append(q1(v, t))
            batches["q2"].append(q2(v, t1, t2))
            batches["q3"].append(q3(v, t))
            batches["q4"].append(q4(v, t1, t2))
        for qn, lines in batches.items():
            path = os.path.join(args.out_dir, f"{qn}_{freq}.txt")
            with open(path, "w") as f:
                f.write("\n".join(lines) + "\n")
            h = hashlib.sha256(open(path, "rb").read()).hexdigest()
            manifest["files"][os.path.basename(path)] = {"lines": len(lines), "sha256": h}
    mpath = os.path.join(args.out_dir, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)

    # Q4 区间长度变体（对齐 AeonG Fig.9(d)）：--intervals 1,20,50,100,150,200
    if args.intervals:
        for iv in [int(x) for x in args.intervals.split(",")]:
            rng2 = random.Random(args.seed + iv)
            vids = [rng2.choice(hot + warm + cold) for _ in range(args.per_type)]
            lines = []
            for v in vids:
                t1 = rng2.randint(args.time_min, args.time_max - iv)
                lines.append(q4(v, t1, t1 + iv))
            path = os.path.join(args.out_dir, f"q4_mix_int{iv}.txt")
            with open(path, "w") as f:
                f.write("\n".join(lines) + "\n")
            h = hashlib.sha256(open(path, "rb").read()).hexdigest()
            manifest["files"][os.path.basename(path)] = {"lines": len(lines), "sha256": h}
        with open(mpath, "w") as f:
            json.dump(manifest, f, indent=2)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
