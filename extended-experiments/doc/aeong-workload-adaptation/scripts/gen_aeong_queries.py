#!/usr/bin/env python3
"""把冻结的 PETGraphDB 查询集转成 AeonG TT 语法查询集（README §5-P4）。

对照原则：与 PETGraphDB 侧冻结文件逐行同顶点（第 i 行 = 同一顶点）；
时间不复用 PETGraphDB 的逻辑时钟（行号），而是在 AeonG 提交时间戳窗口
[ts-lo, ts-hi] 内重新均匀采样（与 AeonG 原评测方法一致：min/max 内随机），
区间长取冻结文件对应的 interval（q4_mix_int<N> → N，其余 100）。

用法: gen_aeong_queries.py --src-dir <冻结queries目录> --out-dir <输出目录> \
         --ts-lo N --ts-hi N [--seed 42]
"""
import argparse
import hashlib
import json
import os
import random
import re

RE_Q1 = re.compile(r"^MATCH \(r:User \{id:(\d+)\}\) WHERE tp\.getPoint\(r, 'valid', \d+\) = 1")
RE_Q2 = re.compile(r"^MATCH \(r:User \{id:(\d+)\}\) CALL tp\.get\(r, 'valid', \d+, \d+\)")
RE_Q3 = re.compile(r"^MATCH \(n:User \{id:(\d+)\}\)-\[e:Friend\]->\(r\) WHERE tp\.getPoint\(e, 'valid', \d+\) = 1")
RE_Q4 = re.compile(r"^MATCH \(n:User \{id:(\d+)\}\)-\[e:Friend\]->\(r\) CALL tp\.get\(e, 'valid', \d+, \d+\)")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ts-lo", type=int, required=True)
    ap.add_argument("--ts-hi", type=int, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = {}
    rng = random.Random(args.seed)

    for name in sorted(os.listdir(args.src_dir)):
        if not name.endswith(".txt"):
            continue
        m = re.match(r"q(\d)_", name)
        assert m, name
        qtype = int(m.group(1))
        m_int = re.search(r"_int(\d+)\.txt$", name)
        interval = int(m_int.group(1)) if m_int else 100

        lines_out = []
        with open(os.path.join(args.src_dir, name)) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if qtype in (1, 2):
                    mm = (RE_Q1 if qtype == 1 else RE_Q2).match(line)
                else:
                    mm = (RE_Q3 if qtype == 3 else RE_Q4).match(line)
                assert mm, f"{name}: unmatched line {line!r}"
                vid = mm.group(1)
                if qtype in (1, 3):
                    t = rng.randint(args.ts_lo, args.ts_hi)
                    tt = f" TT AS {t}"
                else:
                    t1 = rng.randint(args.ts_lo, args.ts_hi - interval)
                    tt = f" TT FROM {t1} TO {t1 + interval}"
                head = (f"MATCH (r:User{{id : {vid}}}) " if qtype in (1, 2)
                        else f"MATCH (n:User{{id : {vid}}})-[e]->(r) ")
                ret = " RETURN r;" if qtype in (1, 2) else " RETURN n,e,r;"
                lines_out.append(head + tt + ret)

        out_path = os.path.join(args.out_dir, name)
        with open(out_path, "w") as f:
            f.write("\n".join(lines_out) + "\n")
        manifest[name] = {"lines": len(lines_out), "interval": interval,
                          "sha256": sha256_of(out_path)}

    with open(os.path.join(args.out_dir, "manifest.json"), "w") as f:
        json.dump({"ts_lo": args.ts_lo, "ts_hi": args.ts_hi, "seed": args.seed,
                   "files": manifest}, f, indent=2)
    for k, v in manifest.items():
        print(f"{k}: lines={v['lines']} sha256={v['sha256'][:16]}")


if __name__ == "__main__":
    main()
