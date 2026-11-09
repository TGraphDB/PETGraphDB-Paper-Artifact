#!/usr/bin/env python3
"""T-LDBC 导入后时态化：把会被操作流修改的 4 个属性初始化为 [0, MAX) 时态属性。

Person: locationIP + browserUsed（tp.setMulti 一句双属性）
Comment/Post: content
基线 t0=0（操作流 t 从 1 起，覆盖语义下 [0,MAX) 是最初值区间）。
UNWIND 批量（每批 2000），按 id 索引锚定。

用法: temporalize_ldbc.py --bolt bolt://server:17687 [--batch 2000]
"""
import argparse
import time

from neo4j import GraphDatabase

MAX = 2147483647


def batches(session, query_read, batch_size):
    # 先取全量 id+属性（sf0.1 规模 288K 行，内存可容），再分批写入
    rows = session.run(query_read).values()
    for i in range(0, len(rows), batch_size):
        yield rows[i:i + batch_size]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolt", required=True)
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password", required=True)
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()

    driver = GraphDatabase.driver(args.bolt, auth=(args.user, args.password))
    # 注意：PETGraphDB 的 tp.set 不能写"已存在同名静态属性"的时态属性
    # （TPSNHException: fail to get stored temporal property type），
    # 因此每批先 REMOVE 静态属性再 tp.set（值已在行内捕获）。README §6 踩坑。
    jobs = [
        ("Person", "MATCH (p:Person) RETURN p.id AS id, p.locationIP AS a, p.browserUsed AS b",
         "UNWIND $rows AS row MATCH (p:Person {id: row[0]}) REMOVE p.locationIP REMOVE p.browserUsed WITH p, row "
         "CALL tp.setMulti(p, ['locationIP','browserUsed'], 0, $max, [row[1], row[2]]) YIELD count WITH sum(count) AS n RETURN n"),
        ("Comment", "MATCH (c:Comment) RETURN c.id AS id, c.content AS a",
         "UNWIND $rows AS row MATCH (c:Comment {id: row[0]}) REMOVE c.content WITH c, row "
         "CALL tp.set(c, 'content', 0, $max, row[1]) YIELD count WITH sum(count) AS n RETURN n"),
        ("Post", "MATCH (c:Post) RETURN c.id AS id, c.content AS a",
         "UNWIND $rows AS row MATCH (c:Post {id: row[0]}) REMOVE c.content WITH c, row "
         "CALL tp.set(c, 'content', 0, $max, row[1]) YIELD count WITH sum(count) AS n RETURN n"),
    ]
    with driver.session() as s:
        for label, q_read, q_write in jobs:
            t0 = time.time()
            total = 0
            for batch in batches(s, q_read, args.batch):
                n = s.run(q_write, rows=batch, max=MAX).single()[0]
                total += n
            print(f"{label}: temporalized {total} entities in {time.time()-t0:.1f}s", flush=True)
        # 完备性校验
        checks = [
            ("Person.locationIP", "MATCH (p:Person) WHERE tp.getPoint(p,'locationIP',0) IS NULL RETURN count(*)"),
            ("Person.browserUsed", "MATCH (p:Person) WHERE tp.getPoint(p,'browserUsed',0) IS NULL RETURN count(*)"),
            ("Comment.content", "MATCH (c:Comment) WHERE tp.getPoint(c,'content',0) IS NULL RETURN count(*)"),
            ("Post.content", "MATCH (c:Post) WHERE tp.getPoint(c,'content',0) IS NULL RETURN count(*)"),
        ]
        for name, q in checks:
            bad = s.run(q).single()[0]
            print(f"check {name}: missing={bad} {'OK' if bad == 0 else 'FAIL'}", flush=True)
    driver.close()


if __name__ == "__main__":
    main()
