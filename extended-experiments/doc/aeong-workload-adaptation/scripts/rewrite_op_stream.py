#!/usr/bin/env python3
"""把 AeonG T-mgBench 图操作流改写为 PETGraphDB 语句（注入逻辑时间戳）。

输入: AeonG create_graph_op_queries.py 产出的 cypher.txt（每行一条语句）。
输出: PETGraphDB 版语句文件，t = 1 起的行号（t0=0 保留给导入基线）。

改写规则（doc/aeong-workload-adaptation/README.md §3.2）:
- update   SET n.completion_percentage=v  -> tp.set(n,'completion_percentage',t,MAX,v)
- create_v CREATE (n:User{id}) SET cp=v    -> CREATE + tp.setMulti(['completion_percentage','valid'],[v,1])
- create_e CREATE (a)-[:Friend]->(b)       -> CREATE + tp.set(e,'valid',t,MAX,1)
- delete_e DELETE e                        -> tp.set(e,'valid',t,MAX,0)  （逻辑删除）

用法: rewrite_op_stream.py <input> <output>
"""
import hashlib
import re
import sys

MAX = 2147483647

RE_UPDATE = re.compile(
    r"^\s*MATCH \(n:User \{id:(\d+)\}\) SET n\.completion_percentage = (\d+);\s*$")
RE_CREATE_V = re.compile(
    r"^\s*CREATE \(n:User \{id : (\d+)\}\) set n\.completion_percentage = (\d+) RETURN n ;\s*$")
RE_CREATE_E = re.compile(
    r"^\s*MATCH \(a:User \{id:(\d+)\}\), \(b:User \{id: ?(\d+)\}\) CREATE \(a\)-\[:Friend\]->\(b\);\s*$")
RE_DELETE_E = re.compile(
    r"^\s*MATCH \(n:User \{id:(\d+)\}\)-\[e\]->\(m:User \{id:(\d+)\}\) DELETE e\s*;\s*$")


def rewrite(line, t):
    m = RE_UPDATE.match(line)
    if m:
        return (f"MATCH (n:User {{id:{m.group(1)}}}) "
                f"CALL tp.set(n, 'completion_percentage', {t}, {MAX}, {m.group(2)}) "
                f"YIELD count RETURN count;")
    m = RE_CREATE_V.match(line)
    if m:
        return (f"CREATE (n:User {{id: {m.group(1)}}}) WITH n "
                f"CALL tp.setMulti(n, ['completion_percentage', 'valid'], {t}, {MAX}, "
                f"[{m.group(2)}, 1]) YIELD count RETURN count;")
    m = RE_CREATE_E.match(line)
    if m:
        return (f"MATCH (a:User {{id:{m.group(1)}}}), (b:User {{id: {m.group(2)}}}) "
                f"CREATE (a)-[e:Friend]->(b) WITH e "
                f"CALL tp.set(e, 'valid', {t}, {MAX}, 1) YIELD count RETURN count;")
    m = RE_DELETE_E.match(line)
    if m:
        return (f"MATCH (n:User {{id:{m.group(1)}}})-[e:Friend]->(m:User {{id:{m.group(2)}}}) "
                f"CALL tp.set(e, 'valid', {t}, {MAX}, 0) YIELD count RETURN count;")
    raise ValueError(f"line {t} unmatched: {line!r}")


def main():
    src, dst = sys.argv[1], sys.argv[2]
    stats = {"update": 0, "create_v": 0, "create_e": 0, "delete_e": 0}
    out = []
    with open(src) as f:
        for i, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            new = rewrite(line, i)
            if new.startswith("MATCH (n:User") and "'completion_percentage'" in new:
                stats["update"] += 1
            elif "setMulti" in new:
                stats["create_v"] += 1
            elif "CREATE (a)-[e:Friend]" in new:
                stats["create_e"] += 1
            else:
                stats["delete_e"] += 1
            out.append(new)
    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")
    h = hashlib.sha256()
    with open(dst, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    n = len(out)
    print(f"total={n} t_window=[1,{n}]")
    print(f"update={stats['update']} create_v={stats['create_v']} "
          f"create_e={stats['create_e']} delete_e={stats['delete_e']}")
    print(f"sha256={h.hexdigest()}")


if __name__ == "__main__":
    main()
