#!/usr/bin/env python3
"""把 AeonG T-LDBC 操作流改写为 PETGraphDB 语句（注入逻辑时间戳，README §6）。

AeonG T-LDBC 是纯属性 update 流（create/delete 路径在其生成器里有 bug 且
默认不启用）。被修改的属性时态化：Person.locationIP / Person.browserUsed /
Comment.content / Post.content。t = 行号（t0=0 保留给导入基线），MAX=Int.Max。

用法: rewrite_ldbc_ops.py <aeong_cypher.txt> <petgraph_ops.txt>
"""
import hashlib
import re
import sys

MAX = 2147483647
RE = re.compile(
    r"^MATCH \([pc]:(Person|Comment|Post) \{id: (\d+)\}\) SET [pc]\.(\w+) ='(.*)';$")


def rewrite(line, t):
    m = RE.match(line)
    if not m:
        raise ValueError(f"line {t} unmatched: {line!r}")
    label, vid, prop, val = m.groups()
    var = "p" if label == "Person" else "c"
    return (f"MATCH ({var}:{label} {{id: {vid}}}) "
            f"CALL tp.set({var}, '{prop}', {t}, {MAX}, '{val}') YIELD count RETURN count;")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src, dst = sys.argv[1], sys.argv[2]
    stats = {}
    out = []
    with open(src) as f:
        for i, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            new = rewrite(line, i)
            prop = re.search(r"tp\.set\(\w+, '(\w+)'", new).group(1)
            stats[prop] = stats.get(prop, 0) + 1
            out.append(new)
    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")
    n = len(out)
    print(f"total={n} t_window=[1,{n}] stats={stats}")
    print(f"sha256={sha256_of(dst)}")


if __name__ == "__main__":
    main()
