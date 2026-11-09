#!/usr/bin/env python3
"""PETGraphDB T-mgBench 数据导入器（客户端运行，打 server 的 Bolt）。

流程（README §6-P1）：
1. 建 :User(id) 索引；
2. 解析 Pokec small 导入文件（10000 点 + 121716 边），批量 UNWIND 导入；
3. 同时初始化时态属性：顶点 completion_percentage=[0,MAX)=初值、valid=[0,MAX)=1；
   边 valid=[0,MAX)=1；
4. 完备性计数校验（tp.getPoint 全量抽查，属性缺失会报错/漏计，此处必须为零）；
5. 输出 JSON 报告（计数、时延、基线时间轴 t0=0）。

用法: import_dataset.py --bolt bolt://server:17687 --dataset pokec_small_import.cypher
"""
import argparse
import json
import re
import time

from neo4j import GraphDatabase

MAX = 2147483647
RE_VERTEX = re.compile(
    r'CREATE \(:User \{id: (\d+), completion_percentage: (\d+), gender: "(\w+)", age: (\d+)\}\);')
RE_EDGE = re.compile(
    r"MATCH \(n:User \{id: (\d+)\}\), \(m:User \{id: (\d+)\}\) CREATE \(n\)-\[e: Friend\]->\(m\);")


def parse_dataset(path):
    vertices, edges, bad = [], [], 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line == ";":
                continue
            m = RE_VERTEX.match(line)
            if m:
                vertices.append({"id": int(m.group(1)), "cp": int(m.group(2)),
                                 "gender": m.group(3), "age": int(m.group(4))})
                continue
            m = RE_EDGE.match(line)
            if m:
                edges.append({"f": int(m.group(1)), "t": int(m.group(2))})
                continue
            bad += 1
    return vertices, edges, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolt", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--vertex-batch", type=int, default=1000)
    ap.add_argument("--edge-batch", type=int, default=200)
    args = ap.parse_args()

    drv = GraphDatabase.driver(args.bolt, auth=("neo4j", args.password))
    report = {"dataset": args.dataset, "t0": 0, "phases": {}}

    vertices, edges, bad = parse_dataset(args.dataset)
    report["parsed"] = {"vertices": len(vertices), "edges": len(edges), "unmatched_lines": bad}
    assert bad == 0, f"dataset has {bad} unmatched lines"

    t_start = time.perf_counter()
    with drv.session() as s:
        s.run("CREATE INDEX ON :User(id)").consume()
    report["phases"]["index_seconds"] = round(time.perf_counter() - t_start, 2)

    t = time.perf_counter()
    with drv.session() as s:
        for i in range(0, len(vertices), args.vertex_batch):
            rows = vertices[i:i + args.vertex_batch]
            s.run(
                "UNWIND $rows AS row CREATE (n:User {id: row.id, gender: row.gender, age: row.age}) "
                "WITH n, row CALL tp.setMulti(n, ['completion_percentage', 'valid'], 0, $MAX, "
                "[row.cp, 1]) YIELD count RETURN sum(count)",
                rows=rows, MAX=MAX).consume()
    report["phases"]["vertices_seconds"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    with drv.session() as s:
        for i in range(0, len(edges), args.edge_batch):
            rows = edges[i:i + args.edge_batch]
            s.run(
                "UNWIND $rows AS row MATCH (a:User {id: row.f}), (b:User {id: row.t}) "
                "CREATE (a)-[e:Friend]->(b) WITH e, row CALL tp.set(e, 'valid', 0, $MAX, 1) "
                "YIELD count RETURN sum(count)",
                rows=rows, MAX=MAX).consume()
    report["phases"]["edges_seconds"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    with drv.session() as s:
        report["check"] = {
            "user_count": s.run("MATCH (n:User) RETURN count(n) AS c").single()["c"],
            "edge_count": s.run("MATCH ()-[e:Friend]->() RETURN count(e) AS c").single()["c"],
            "users_valid_at_0": s.run(
                "MATCH (n:User) WHERE tp.getPoint(n, 'valid', 0) = 1 RETURN count(n) AS c"
            ).single()["c"],
            "users_cp_missing_at_0": s.run(
                "MATCH (n:User) WHERE tp.getPoint(n, 'completion_percentage', 0) IS NULL "
                "RETURN count(n) AS c").single()["c"],
            "edges_valid_at_0": s.run(
                "MATCH ()-[e:Friend]->() WHERE tp.getPoint(e, 'valid', 0) = 1 "
                "RETURN count(e) AS c").single()["c"],
        }
    report["phases"]["check_seconds"] = round(time.perf_counter() - t, 2)

    ok = (report["check"]["user_count"] == len(vertices)
          and report["check"]["edge_count"] == len(edges)
          and report["check"]["users_valid_at_0"] == len(vertices)
          and report["check"]["users_cp_missing_at_0"] == 0
          and report["check"]["edges_valid_at_0"] == len(edges))
    report["init_complete"] = ok
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit("INIT INCOMPLETE — see check block")


if __name__ == "__main__":
    main()
