#!/usr/bin/env python3
"""生成 T-LDBC IS1–IS7 双侧查询集（README §6 Phase 2）。

PETGraphDB 版：静态 MATCH + tp.getPoint 取时态属性在 t 的值
（T-LDBC 操作流为纯属性 update，无结构变化，故无需 valid 生命周期属性；
 被时态化的属性：Person.locationIP/browserUsed、Comment/Post.content）。
AeonG 版：其原生 TT AS ts 模板（忠实 AeonG T-LDBC 查询形态）。

顶点：IS1–IS3 用 person 的 hot/warm/cold 清单，IS4–IS7 用 comment 清单；
mix = 34 hot + 33 warm + 33 cold（修复 AeonG 生成器 99 顶点 + cold 误用 bug）。
时间：PETG 逻辑时钟 [1,1500000]；AeonG 提交时间戳窗口（跑完造库后回填）。
同一 seed 下双侧逐行同顶点。

用法: gen_ldbc_queries.py --peak-dir <ldbc-gen/peak_vertices> --out-dir <dir> \
         --format petgraph|aeong --time-min N --time-max N [--num-op 1000000]
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re

IS_PERSON = {1, 2, 3}
IS_COMMENT = {4, 5, 6, 7}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_ids(path):
    ids = []
    with open(path) as f:
        for row in csv.reader(f):
            if row:
                ids.append(row[0])
    return ids


def petgraph_query(is_idx, vid, t):
    P = f"tp.getPoint"
    if is_idx == 1:
        return (f"MATCH (n:Person {{id:{vid}}})-[:IS_LOCATED_IN]->(p:Place) "
                f"RETURN n.firstName AS firstName, n.lastName AS lastName, n.birthday AS birthday, "
                f"{P}(n,'locationIP',{t}) AS locationIp, {P}(n,'browserUsed',{t}) AS browserUsed, "
                f"n.gender AS gender, n.creationDate AS creationDate, p.id AS cityId;")
    if is_idx == 2:
        return (f"MATCH (:Person {{id:{vid}}})<-[:HAS_CREATOR]-(m)-[:REPLY_OF*0..]->(p:Post) "
                f"MATCH (p)-[:HAS_CREATOR]->(c) "
                f"RETURN m.id AS messageId, "
                f"CASE {P}(m,'content',{t},'') IS NOT NULL WHEN true THEN {P}(m,'content',{t},'') ELSE m.imageFile END AS messageContent, "
                f"m.creationDate AS messageCreationDate, p.id AS originalPostId, c.id AS originalPostAuthorId, "
                f"c.firstName AS originalPostAuthorFirstName, c.lastName AS originalPostAuthorLastName "
                f"ORDER BY messageCreationDate DESC LIMIT 10;")
    if is_idx == 3:
        return (f"MATCH (n:Person {{id:{vid}}})-[r:KNOWS]-(friend) "
                f"RETURN friend.id AS personId, friend.firstName AS firstName, friend.lastName AS lastName, "
                f"r.creationDate AS friendshipCreationDate "
                f"ORDER BY friendshipCreationDate DESC, toInteger(personId) ASC;")
    if is_idx == 4:
        return (f"MATCH (m:Message {{id:{vid}}}) "
                f"RETURN CASE {P}(m,'content',{t},'') IS NOT NULL WHEN true THEN {P}(m,'content',{t},'') "
                f"ELSE m.imageFile END AS messageContent, m.creationDate AS messageCreationDate;")
    if is_idx == 5:
        return (f"MATCH (m:Message {{id:{vid}}})-[:HAS_CREATOR]->(p:Person) "
                f"RETURN p.id, p.firstName, p.lastName;")
    label = "Message" if is_idx == 6 else "Comment"
    return (f"MATCH (m:{label} {{id:{vid}}})<-[:REPLY_OF]-(c:Comment)-[:HAS_CREATOR]->(p:Person) "
            f"OPTIONAL MATCH (m)-[:HAS_CREATOR]->(a:Person)-[r:KNOWS]-(p) "
            f"RETURN c.id AS commentId, {P}(c,'content',{t}) AS commentContent, c.creationDate AS commentCreationDate, "
            f"p.id AS replyAuthorId, p.firstName AS replyAuthorFirstName, p.lastName AS replyAuthorLastName, "
            f"CASE r WHEN null THEN false ELSE true END AS replyAuthorKnowsOriginalMessageAuthor "
            f"ORDER BY commentCreationDate DESC, toInteger(replyAuthorId) ASC;")


def aeong_query(is_idx, vid, ts):
    if is_idx == 1:
        return (f"MATCH (n:Person {{id:{vid}}})-[:IS_LOCATED_IN]->(p:Place) TT AS {ts} "
                f"RETURN n.firstName AS firstName, n.lastName AS lastName, n.birthday AS birthday, "
                f"n.locationIP AS locationIp, n.browserUsed AS browserUsed, n.gender AS gender, "
                f"n.creationDate AS creationDate, p.id AS cityId;")
    if is_idx == 2:
        return (f"MATCH (:Person {{id:{vid}}})<-[:HAS_CREATOR]-(m)-[:REPLY_OF*0..]->(p:Post) "
                f"MATCH (p)-[:HAS_CREATOR]->(c) TT AS {ts} "
                f"RETURN m.id AS messageId, "
                f"CASE m.content IS NOT NULL WHEN true THEN m.content ELSE m.imageFile END AS messageContent, "
                f"m.creationDate AS messageCreationDate, p.id AS originalPostId, c.id AS originalPostAuthorId, "
                f"c.firstName AS originalPostAuthorFirstName, c.lastName AS originalPostAuthorLastName "
                f"ORDER BY messageCreationDate DESC LIMIT 10;")
    if is_idx == 3:
        return (f"MATCH (n:Person {{id:{vid}}})-[r:KNOWS]-(friend) TT AS {ts} "
                f"RETURN friend.id AS personId, friend.firstName AS firstName, friend.lastName AS lastName, "
                f"r.creationDate AS friendshipCreationDate "
                f"ORDER BY friendshipCreationDate DESC, toInteger(personId) ASC;")
    if is_idx == 4:
        return (f"MATCH (m:Message {{id:{vid}}}) TT AS {ts} "
                f"RETURN CASE m.content IS NOT NULL WHEN true THEN m.content ELSE m.imageFile END AS messageContent, "
                f"m.creationDate AS messageCreationDate;")
    if is_idx == 5:
        return (f"MATCH (m:Message {{id:{vid}}})-[:HAS_CREATOR]->(p:Person) TT AS {ts} "
                f"RETURN p.id, p.firstName, p.lastName;")
    label = "Message" if is_idx == 6 else "Comment"
    return (f"MATCH (m:{label} {{id:{vid}}})<-[:REPLY_OF]-(c:Comment)-[:HAS_CREATOR]->(p:Person) "
            f"OPTIONAL MATCH (m)-[:HAS_CREATOR]->(a:Person)-[r:KNOWS]-(p) TT AS {ts} "
            f"RETURN c.id AS commentId, c.content AS commentContent, c.creationDate AS commentCreationDate, "
            f"p.id AS replyAuthorId, p.firstName AS replyAuthorFirstName, p.lastName AS replyAuthorLastName, "
            f"CASE r WHEN null THEN false ELSE true END AS replyAuthorKnowsOriginalMessageAuthor "
            f"ORDER BY commentCreationDate DESC, toInteger(replyAuthorId) ASC;")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peak-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--format", choices=["petgraph", "aeong"], required=True)
    ap.add_argument("--time-min", type=int, required=True)
    ap.add_argument("--time-max", type=int, required=True)
    ap.add_argument("--num-op", type=int, default=1000000)
    ap.add_argument("--per-is", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    def pool(kind, is_idx):
        entity = "person" if is_idx in IS_PERSON else "comment"
        return read_ids(os.path.join(
            args.peak_dir,
            f"update_{entity}_Num{args.num_op}_update_count_{kind}.csv"))

    os.makedirs(args.out_dir, exist_ok=True)
    rng = random.Random(args.seed)
    manifest = {}
    gen = petgraph_query if args.format == "petgraph" else aeong_query

    for is_idx in range(1, 8):
        hot, warm, cold = pool("hot", is_idx), pool("warm", is_idx), pool("cold", is_idx)
        assert hot and warm and cold, f"IS{is_idx}: empty frequency pool"
        lines = []
        for i in range(args.per_is):
            if i < 34:
                vid = rng.choice(hot)
            elif i < 67:
                vid = rng.choice(warm)
            else:
                vid = rng.choice(cold)
            t = rng.randint(args.time_min, args.time_max)
            lines.append(gen(is_idx, vid, t))
        name = f"IS{is_idx}.txt"
        path = os.path.join(args.out_dir, name)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        manifest[name] = {"lines": len(lines), "sha256": sha256_of(path)}

    with open(os.path.join(args.out_dir, "manifest.json"), "w") as f:
        json.dump({"format": args.format, "time_window": [args.time_min, args.time_max],
                   "seed": args.seed, "files": manifest}, f, indent=2)
    for k, v in manifest.items():
        print(f"{k}: lines={v['lines']} sha256={v['sha256'][:16]}")


if __name__ == "__main__":
    main()
