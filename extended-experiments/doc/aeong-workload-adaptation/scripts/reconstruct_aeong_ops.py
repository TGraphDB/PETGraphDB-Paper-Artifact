#!/usr/bin/env python3
"""从冻结的 petgraph_ops.txt 反推 AeonG 原始操作流（README §5-P4）。

AeonG 侧的 cypher.txt 原始文件已丢失，但 rewrite_op_stream.py 的改写是逐行
可逆的（每行恰好映射回一条 AeonG 语句）。本脚本做逆映射，并用
"正向改写结果必须与 petgraph_ops.txt 字节级一致（SHA-256 相等）"自证。

用法: reconstruct_aeong_ops.py <petgraph_ops.txt> <aeong_ops.txt>
"""
import hashlib
import re
import sys

RE_UPDATE = re.compile(
    r"^MATCH \(n:User \{id:(\d+)\}\) CALL tp\.set\(n, 'completion_percentage', \d+, 2147483647, (\d+)\) YIELD count( RETURN count)?;$")
RE_CREATE_V = re.compile(
    r"^CREATE \(n:User \{id: (\d+)\}\) WITH n CALL tp\.setMulti\(n, \['completion_percentage', 'valid'\], \d+, 2147483647, \[(\d+), 1\]\) YIELD count( RETURN count)?;$")
RE_CREATE_E = re.compile(
    r"^MATCH \(a:User \{id:(\d+)\}\), \(b:User \{id: (\d+)\}\) CREATE \(a\)-\[e:Friend\]->\(b\) WITH e CALL tp\.set\(e, 'valid', \d+, 2147483647, 1\) YIELD count( RETURN count)?;$")
RE_DELETE_E = re.compile(
    r"^MATCH \(n:User \{id:(\d+)\}\)-\[e:Friend\]->\(m:User \{id:(\d+)\}\) CALL tp\.set\(e, 'valid', \d+, 2147483647, 0\) YIELD count( RETURN count)?;$")


def invert(line):
    m = RE_UPDATE.match(line)
    if m:
        return f"MATCH (n:User {{id:{m.group(1)}}}) SET n.completion_percentage = {m.group(2)};", "update"
    m = RE_CREATE_V.match(line)
    if m:
        return (f"CREATE (n:User {{id : {m.group(1)}}}) set n.completion_percentage = {m.group(2)} RETURN n ;",
                "create_v")
    m = RE_CREATE_E.match(line)
    if m:
        return (f"MATCH (a:User {{id:{m.group(1)}}}), (b:User {{id: {m.group(2)}}}) CREATE (a)-[:Friend]->(b);",
                "create_e")
    m = RE_DELETE_E.match(line)
    if m:
        return f"MATCH (n:User {{id:{m.group(1)}}})-[e]->(m:User {{id:{m.group(2)}}}) DELETE e ;", "delete_e"
    raise ValueError(f"unmatched line: {line!r}")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src, dst = sys.argv[1], sys.argv[2]
    stats = {"update": 0, "create_v": 0, "create_e": 0, "delete_e": 0}
    out = []
    with open(src) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            new, kind = invert(line)
            stats[kind] += 1
            out.append(new)
    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")
    n = len(out)
    print(f"total={n} stats={stats}")
    print(f"sha256(aeong_ops)={sha256_of(dst)}")

    # 自证：用正向改写复现 petgraph_ops.txt
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rewrite_op_stream as rw
    fwd = dst + ".fwd_check"
    import io
    buf = []
    for i, line in enumerate(out, start=1):
        buf.append(rw.rewrite(line, i))
    with open(fwd, "w") as f:
        f.write("\n".join(buf) + "\n")
    a, b = sha256_of(src), sha256_of(fwd)
    print(f"sha256(petgraph_ops)   ={a}")
    print(f"sha256(forward_rewrite)={b}")
    print("VERIFY:", "OK byte-identical" if a == b else "MISMATCH")


if __name__ == "__main__":
    import os
    main()
