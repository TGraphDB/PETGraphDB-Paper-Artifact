#!/usr/bin/env python3
"""把 Memgraph 逐句 ldbc.cypher 转成批量 UNWIND 语句（每行一个事务）。

背景：AeonG 2.2 此版本逐语句导入边约 2 条/s（每事务固定开销），全量 148 万边
不可行；批量 UNWIND 实测 1454 边/0.1s。本转换保证数据等价：
- 顶点：CREATE 语句原样收进 UNWIND 列表（属性字面量不动），SET n = r
- 边：MATCH 两端按 (label,label,type,props签名) 分组，行内 f/t + 属性表达式原样
产物：<out>.vertices.cypher / <out>.edges.cypher，断言点数/边数与 SIZES 一致。

用法: ldbc_batch_convert.py <ldbc.cypher> <out_prefix> [--batch 2000]
"""
import argparse
import random
import re
import sys
from collections import defaultdict

RE_VERTEX = re.compile(r"^CREATE \(((:[A-Za-z]+)+) \{(.*)\}\);$")
RE_EDGE = re.compile(
    r"^MATCH \(n1:([A-Za-z]+) \{id:(\d+)\}\), \(n2:([A-Za-z]+) \{id:(\d+)\}\) "
    r"CREATE \(n1\)-\[:(\w+)(?:\{(.*)\})?\]->\(n2\);$")
RE_PROP = re.compile(r"(\w+)\s*:\s*(localDateTime\(\"[^\"]*\"\)|toInteger\(\d+\)|[^,]+)")

EXPECT = {"vertices": 327588, "edges": 1477965}  # sf0.1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--shuffle-edges", action="store_true")
    args = ap.parse_args()

    v_groups = defaultdict(list)   # labels -> [props_inner, ...]
    e_groups = defaultdict(list)   # (l1,l2,typ,propnames) -> [(f,t,[exprs])]
    n_v = n_e = 0
    bad = 0
    with open(args.src) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.strip() == ";":
                continue
            m = RE_VERTEX.match(line)
            if m:
                v_groups[m.group(1)].append(m.group(3))
                n_v += 1
                continue
            m = RE_EDGE.match(line)
            if m:
                l1, f_id, l2, t_id, typ = m.groups()[:5]
                props = m.group(6) or ""
                pairs = RE_PROP.findall(props) if props.strip() else []
                names = tuple(p[0] for p in pairs)
                exprs = tuple(p[1].strip() for p in pairs)
                e_groups[(l1, l2, typ, names)].append((f_id, t_id, exprs))
                n_e += 1
                continue
            bad += 1
            if bad <= 3:
                print("UNMATCHED:", line[:120], file=sys.stderr)

    print(f"parsed vertices={n_v} edges={n_e} unmatched={bad}")
    assert n_v == EXPECT["vertices"], f"vertex count {n_v}"
    assert n_e == EXPECT["edges"], f"edge count {n_e}"

    with open(args.out + ".vertices.cypher", "w") as out:
        for labels, rows in sorted(v_groups.items()):
            for i in range(0, len(rows), args.batch):
                chunk = rows[i:i + args.batch]
                maps = ",".join("{" + r + "}" for r in chunk)
                out.write(f"UNWIND [{maps}] AS r CREATE (n{labels}) SET n = r;\n")

    if args.shuffle_edges:
        rng = random.Random(42)
        for rows in e_groups.values():
            rng.shuffle(rows)
    with open(args.out + ".edges.cypher", "w") as out:
        for (l1, l2, typ, names), rows in sorted(e_groups.items()):
            def rowmap(r):
                f, t, exprs = r  # 每行属性值表达式各不相同，必须按行内联
                ps = "".join(f",p{k}:{e}" for k, e in enumerate(exprs))
                return f"{{f:{f},t:{t}{ps}}}"
            # Memgraph 2.2 的 CREATE 边属性只接受字面量；变量值必须 CREATE 后 SET
            if names:
                eset = ", ".join(f"e.{n} = r.p{k}" for k, n in enumerate(names))
                create = f"CREATE (a)-[e:{typ}]->(b) SET {eset}"
            else:
                create = f"CREATE (a)-[:{typ}]->(b)"
            for i in range(0, len(rows), args.batch):
                chunk = rows[i:i + args.batch]
                maps = ",".join(rowmap(r) for r in chunk)
                out.write(f"UNWIND [{maps}] AS r MATCH (a:{l1} {{id:r.f}}),(b:{l2} {{id:r.t}}) {create};\n")
    print("written", args.out + ".vertices.cypher", args.out + ".edges.cypher")


if __name__ == "__main__":
    main()
