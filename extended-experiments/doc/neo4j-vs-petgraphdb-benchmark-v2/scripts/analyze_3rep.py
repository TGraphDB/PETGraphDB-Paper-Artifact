#!/usr/bin/env python3
"""
SF10 三轮配对实验分析器（AUDIT-AND-PROTOCOL.md §2.5 统计口径）。

输入（client 本地，缺文件则跳过并注明，不伪造）：
  Interactive : ldbc_snb_interactive_v1_impls-main/cypher/results-archive/sf10-{neo,pet}-{ro,rw}-rep{N}-results.json
  微基准      : results-microbench-paired/sf10-{neo,pet}-rep{N}-{param,agg}_summary.json + *_raw.csv
  图算法      : results-ga-analysis-paired/sf10-{neo,pet}-garep{N}_graphalgos.json
  分析查询    : results-ga-analysis-paired/sf10-{neo,pet}-arep{N}_q*.json + *_raw.csv

输出：
  <out>.md   人读报告（ratio=PET/Neo4j，<1 PET 快；mean/median/p95 并列；
             跨轮 min/max；读/写/算法分组几何平均；配对校验结果）
  <out>.json 机器读全量数据（供 figures 更新与论文数字包）

配对校验（§2.4）：
  微基准  raw CSV 按 params_json 配对，比较 nrecords（逐参数一致）；
  分析    raw CSV 双方同为单线程确定性轮转（warmup 行后第 j 行 = 第 j%N 个参数），
          按行序配对比较 nrecords；
  BFS     reachable_min/median/max 跨系统一致；PR top3 容差 1e-6；WCC components 相等。

不混旧数据：只读本脚本列出的 paired 目录；旧 rep1（results-microbench/ 等）不参与统计。
"""

import argparse
import csv
import glob
import json
import math
import os
import statistics
import sys

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def geo_mean(vals):
    vals = [v for v in vals if v is not None and v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def ratio(a, b):
    """PET/Neo4j；任一侧缺失/非正返回 None。"""
    if a is None or b is None or b <= 0:
        return None
    return a / b


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


# ---------------------------------------------------------------------------
# Interactive（官方 driver 结果）
# ---------------------------------------------------------------------------
# 查询分类：结果 JSON 中名字带后缀（LdbcQuery6、LdbcShortQuery1PersonProfile、
# LdbcUpdate1AddPerson），一律按前缀归类。
def classify_ic(q):
    if q.startswith("LdbcUpdate"):
        return "update"
    if q.startswith("LdbcShortQuery"):
        return "short"
    if q.startswith("LdbcQuery"):
        return "cxread"
    return "other"


CLASS_ORDER = {"cxread": 0, "short": 1, "update": 2}


def load_interactive(arch):
    """返回 {(mode, rep, sys): {qname: {mean, p50, count}}}"""
    out = {}
    for mode in ("ro", "rw"):
        for rep in (1, 2, 3):
            for sysname in ("neo", "pet"):
                p = os.path.join(arch, f"sf10-{sysname}-{mode}-rep{rep}-results.json")
                d = read_json(p)
                if d is None:
                    continue
                m = {}
                for e in d.get("all_metrics", []):
                    rt = e.get("run_time", {})
                    m[e["name"]] = {
                        "mean_ms": rt.get("mean"),
                        "p50_ms": rt.get("50th_percentile"),
                        "p95_ms": rt.get("95th_percentile"),
                        "count": e.get("count"),
                    }
                out[(mode, rep, sysname)] = m
    return out


def section_interactive(data, lines, payload):
    lines.append("## Interactive（单线程 100k op，rep1 为既有归档 + rep2/3 新跑）")
    lines.append("每轮四列：neo mean(ms) / pet mean(ms) / mean ratio / median ratio；"
                 "ratio = PET/Neo4j（读为 ro 模式、更新为 rw 模式）。")
    lines.append("```")
    header = f"{'query':<30}{'class':<8}"
    for rep in (1, 2, 3):
        header += f"{'r' + str(rep) + ' neo':>10}{'r' + str(rep) + ' pet':>10}{'r' + str(rep) + ' r_m':>8}{'r' + str(rep) + ' r_p50':>8}"
    lines.append(header)
    groups = {}
    all_q = sorted({q for (mode, rep, s), m in data.items() for q in m},
                   key=lambda x: (CLASS_ORDER.get(classify_ic(x), 9), x))
    for q in all_q:
        cls = classify_ic(q)
        if cls == "other":
            continue
        mode = "rw" if cls == "update" else "ro"
        row = f"{q:<30}{cls:<8}"
        ratios = []
        for rep in (1, 2, 3):
            neo = data.get((mode, rep, "neo"), {}).get(q)
            pet = data.get((mode, rep, "pet"), {}).get(q)
            r = ratio(pet and pet["mean_ms"], neo and neo["mean_ms"])
            rm = ratio(pet and pet["p50_ms"], neo and neo["p50_ms"])
            if r:
                ratios.append(r)
            row += (f"{(neo or {}).get('mean_ms') or float('nan'):>10.1f}"
                    f"{(pet or {}).get('mean_ms') or float('nan'):>10.1f}"
                    f"{r or float('nan'):>8.3f}{rm or float('nan'):>8.3f}")
        lines.append(row)
        groups.setdefault(cls, []).append((q, ratios))
    lines.append("```")
    lines.append("")
    lines.append("分组几何平均（ratio，跨轮 min–max）：")
    for gname in ("cxread", "short", "update"):
        items = groups.get(gname, [])
        if not items:
            continue
        mode = "rw" if gname == "update" else "ro"
        per_rep = []
        for rep in (1, 2, 3):
            rs = []
            for q, _ in items:
                neo = data.get((mode, rep, "neo"), {}).get(q)
                pet = data.get((mode, rep, "pet"), {}).get(q)
                r = ratio(pet and pet["mean_ms"], neo and neo["mean_ms"])
                if r:
                    rs.append(r)
            g = geo_mean(rs)
            if g:
                per_rep.append((rep, g))
        if per_rep:
            lines.append(f"- {gname}: " +
                         ", ".join(f"r{rep}={fmt(g)}" for rep, g in per_rep) +
                         f"  | min={fmt(min(g for _, g in per_rep))} "
                         f"max={fmt(max(g for _, g in per_rep))}")
    lines.append("")
    payload["interactive"] = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in data.items()}


# ---------------------------------------------------------------------------
# 微基准（19 探针，param/agg 分开前缀）
# ---------------------------------------------------------------------------
READ_PROBES = {"point-by-id", "point-by-internal-id", "expand-1hop-out", "expand-1hop-in",
               "expand-2hop", "khop-3-limited", "shortest-path",
               "agg-person-ip-prefix", "agg-message-year"}
WRITE_PROBES = {"write-set-prop", "write-create-delete"}
AGG_PROBES = {"agg-person-ip-prefix", "agg-message-year"}


def load_micro(outdir):
    """返回 {(rep, sys, kind): {tag: stats}}；kind ∈ param|agg"""
    out = {}
    for rep in (1, 2, 3):
        for sysname in ("neo", "pet"):
            for kind in ("param", "agg"):
                p = os.path.join(outdir, f"sf10-{sysname}-rep{rep}-{kind}_summary.json")
                d = read_json(p)
                if d:
                    out[(rep, sysname, kind)] = d.get("probes", {})
    return out


def section_micro(data, outdir, lines, payload):
    lines.append("## 微基准（19 探针：参数化 9 探针×稀/稠 + 聚合 2 + 写 2；"
                 "warmup 20+reps 100，聚合 5+20）")
    lines.append("ratio = PET mean / Neo mean；同时给出 median ratio。")
    lines.append("```")
    lines.append(f"{'probe.tier':<36}{'r mean-ratio':>12}{'r med-ratio':>12}"
                 f"{'r1':>8}{'r2':>8}{'r3':>8}  mean-ratio min–max")
    tags = sorted({t for (rep, s, k), m in data.items() for t in m})
    groups = {"read": [], "write": []}
    for tag in tags:
        pname = tag.rsplit(".", 1)[0]
        gname = "write" if pname in WRITE_PROBES else "read"
        kind = "agg" if pname in AGG_PROBES else "param"
        mean_rs, med_rs = [], []
        cells = ""
        for rep in (1, 2, 3):
            neo = data.get((rep, "neo", kind), {}).get(tag)
            pet = data.get((rep, "pet", kind), {}).get(tag)
            r = ratio(pet and pet.get("mean_ms"), neo and neo.get("mean_ms"))
            rm = ratio(pet and pet.get("median_ms"), neo and neo.get("median_ms"))
            if r:
                mean_rs.append(r)
            if rm:
                med_rs.append(rm)
            cells += f"{r or float('nan'):>8.3f}"
        lines.append(f"{tag:<36}{fmt(mean_rs[0] if mean_rs else None):>12}"
                     f"{fmt(med_rs[0] if med_rs else None):>12}{cells}"
                     f"  {fmt(min(mean_rs)) if mean_rs else 'n/a'}–{fmt(max(mean_rs)) if mean_rs else 'n/a'}")
        groups[gname].append((tag, mean_rs))
    lines.append("```")
    for gname, items in groups.items():
        per_rep = []
        for rep in (1, 2, 3):
            rs = []
            for tag, _ in items:
                pname = tag.rsplit(".", 1)[0]
                kind = "agg" if pname in ("agg-person-ip-prefix", "agg-message-year") else "param"
                neo = data.get((rep, "neo", kind), {}).get(tag)
                pet = data.get((rep, "pet", kind), {}).get(tag)
                r = ratio(pet and pet.get("mean_ms"), neo and neo.get("mean_ms"))
                if r:
                    rs.append(r)
            g = geo_mean(rs)
            if g:
                per_rep.append((rep, g))
        if per_rep:
            lines.append(f"- {gname} geo mean: " +
                         " ".join(f"r{rep}={fmt(g)}" for rep, g in per_rep) +
                         f"  | min={fmt(min(g for _, g in per_rep))} max={fmt(max(g for _, g in per_rep))}")
    lines.append("")
    payload["microbench"] = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in data.items()}

    # 配对校验：raw CSV 按 params_json 比对 nrecords
    lines.append("配对校验（raw CSV，params_json 配对，nrecords 一致率）：")
    for rep in (1, 2, 3):
        for kind in ("param", "agg"):
            neo_csv = os.path.join(outdir, f"sf10-neo-rep{rep}-{kind}_raw.csv")
            pet_csv = os.path.join(outdir, f"sf10-pet-rep{rep}-{kind}_raw.csv")
            if not (os.path.exists(neo_csv) and os.path.exists(pet_csv)):
                continue
            res = _validate_micro_raw(neo_csv, pet_csv)
            lines.append(f"- rep{rep} {kind}: {res}")
    lines.append("")


def _validate_micro_raw(neo_csv, pet_csv):
    def load(path):
        per = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                key = (row["tag"], row.get("params_json", ""))
                per.setdefault(key, []).append(int(row["nrecords"] or 0))
        return per
    a, b = load(neo_csv), load(pet_csv)
    common = set(a) & set(b)
    if not common:
        return "无共同参数键（检查 params_json 记录）"
    mism = [(k, a[k][0], b[k][0]) for k in common if a[k][0] != b[k][0]]
    return (f"共同参数键 {len(common)}，nrecords 不一致 {len(mism)}"
            + (f"，示例 {mism[:3]}" if mism else ""))


# ---------------------------------------------------------------------------
# 图算法 + 分析查询
# ---------------------------------------------------------------------------

def load_ga(outdir):
    out = {}
    for rep in (1, 2, 3):
        for sysname in ("neo", "pet"):
            p = os.path.join(outdir, f"sf10-{sysname}-garep{rep}_graphalgos.json")
            d = read_json(p)
            if d and d.get("rounds_data"):
                out[(rep, sysname)] = d["rounds_data"][0]
    return out


def section_ga(data, lines, payload):
    lines.append("## 图算法（BFS 50 种子 depth3 cap1e6；PR 20 轮固定 d=0.85；WCC 至收敛）")
    lines.append("ratio = PET total_s / Neo total_s（BFS 取 mean lat）；PR 三口径："
                 "total(20it)、per-iter geo、首轮；WCC 三口径：收敛总耗时、固定前 6 轮、轮数。")
    lines.append("```")
    for algo in ("bfs", "pagerank", "wcc"):
        lines.append(f"[{algo}]")
        for rep in (1, 2, 3):
            neo = data.get((rep, "neo"), {}).get(algo)
            pet = data.get((rep, "pet"), {}).get(algo)
            if not neo or not pet:
                continue
            if algo == "bfs":
                r_mean = ratio(pet["lat"]["mean_s"], neo["lat"]["mean_s"])
                r_med = ratio(pet["lat"]["median_s"], neo["lat"]["median_s"])
                r_p95 = ratio(pet["lat"]["p95_s"], neo["lat"]["p95_s"])
                lines.append(f"  rep{rep}: mean-ratio={fmt(r_mean)} med={fmt(r_med)} "
                             f"p95={fmt(r_p95)} | neo mean={neo['lat']['mean_s']:.3f}s "
                             f"pet mean={pet['lat']['mean_s']:.3f}s failed={neo['n_failed']}/{pet['n_failed']}")
            elif algo == "pagerank":
                r_tot = ratio(pet["total_s"], neo["total_s"])
                r_per = ratio(geo_mean(pet["per_iter_s"]), geo_mean(neo["per_iter_s"]))
                r_1st = ratio(pet["per_iter_s"][0], neo["per_iter_s"][0])
                top_eq = all(abs(x["pr"] - y["pr"]) <= 1e-6
                             for x, y in zip(neo["top3"], pet["top3"]))
                lines.append(f"  rep{rep}: total20-ratio={fmt(r_tot)} periter-geo={fmt(r_per)} "
                             f"iter1={fmt(r_1st)} | neo={neo['total_s']:.1f}s pet={pet['total_s']:.1f}s "
                             f"top3一致(1e-6)={top_eq}")
            else:
                r_tot = ratio(pet["total_s"], neo["total_s"])
                r_6 = ratio(sum(pet["per_iter_s"][:6]), sum(neo["per_iter_s"][:6]))
                lines.append(f"  rep{rep}: conv-total-ratio={fmt(r_tot)} first6-ratio={fmt(r_6)} "
                             f"| neo={neo['total_s']:.1f}s/{neo['iters_run']}it pet={pet['total_s']:.1f}s/"
                             f"{pet['iters_run']}it comp={neo['components']}/{pet['components']}")
        lines.append("")
    lines.append("```")
    payload["graphalgos"] = {f"{k[0]}-{k[1]}": v for k, v in data.items()}


def load_analysis(outdir):
    """返回 {(rep, sys): {qname: stats}}；qname 取自 query_name 字段。"""
    out = {}
    for rep in (1, 2, 3):
        for sysname in ("neo", "pet"):
            m = {}
            for p in glob.glob(os.path.join(outdir, f"sf10-{sysname}-arep{rep}_q*.json")):
                d = read_json(p)
                if d:
                    m[d.get("query_name", os.path.basename(p))] = d
            if m:
                out[(rep, sysname)] = m
    return out


def section_analysis(data, outdir, lines, payload):
    lines.append("## 自定义分析查询 q1–q5（warmup 5 + reps 100 × 冻结参数集；非 BI 声明）")
    lines.append("```")
    lines.append(f"{'query':<28}{'r mean-ratio':>12}{'r med-ratio':>12}{'r1':>8}{'r2':>8}{'r3':>8}"
                 f"  empty(neo/pet, r1)")
    qnames = sorted({q for (rep, s), m in data.items() for q in m})
    for q in qnames:
        mean_rs, med_rs = [], []
        cells = ""
        for rep in (1, 2, 3):
            neo = data.get((rep, "neo"), {}).get(q)
            pet = data.get((rep, "pet"), {}).get(q)
            r = ratio(pet and pet.get("mean_ms"), neo and neo.get("mean_ms"))
            rm = ratio(pet and pet.get("median_ms"), neo and neo.get("median_ms"))
            if r:
                mean_rs.append(r)
            if rm:
                med_rs.append(rm)
            cells += f"{r or float('nan'):>8.3f}"
        neo1 = data.get((1, "neo"), {}).get(q, {})
        pet1 = data.get((1, "pet"), {}).get(q, {})
        lines.append(f"{q:<28}{fmt(mean_rs[0] if mean_rs else None):>12}"
                     f"{fmt(med_rs[0] if med_rs else None):>12}{cells}  "
                     f"{neo1.get('empty_results', '?')}/{pet1.get('empty_results', '?')}")
    lines.append("```")
    for rep in (1, 2, 3):
        rs = []
        for q in qnames:
            neo = data.get((rep, "neo"), {}).get(q)
            pet = data.get((rep, "pet"), {}).get(q)
            r = ratio(pet and pet.get("mean_ms"), neo and neo.get("mean_ms"))
            if r:
                rs.append(r)
        g = geo_mean(rs)
        if g:
            lines.append(f"- analysis geo mean (rep{rep}) = {fmt(g)}")
    lines.append("")
    payload["analysis"] = {f"{k[0]}-{k[1]}": v for k, v in data.items()}

    # 配对校验：分析 raw CSV 行序配对（单线程确定性轮转）
    lines.append("配对校验（分析 raw CSV 行序配对，nrecords 一致率）：")
    for rep in (1, 2, 3):
        neo_csv = os.path.join(outdir, f"sf10-neo-arep{rep}_raw.csv")
        pet_csv = os.path.join(outdir, f"sf10-pet-arep{rep}_raw.csv")
        if not (os.path.exists(neo_csv) and os.path.exists(pet_csv)):
            continue
        res = _validate_analysis_raw(neo_csv, pet_csv)
        lines.append(f"- rep{rep}: {res}")
    lines.append("")


def _validate_analysis_raw(neo_csv, pet_csv):
    def load(path):
        per = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                per.setdefault(row["probe"], []).append(
                    (int(row["nrecords"] or 0), row.get("error", "")))
        return per
    a, b = load(neo_csv), load(pet_csv)
    msgs = []
    for q in sorted(set(a) & set(b)):
        ra, rb = a[q], b[q]
        n = min(len(ra), len(rb))
        mism = sum(1 for i in range(n) if ra[i][0] != rb[i][0] and not ra[i][1] and not rb[i][1])
        msgs.append(f"{q}: 行数 {len(ra)}/{len(rb)}，不一致 {mism}")
    return "；".join(msgs) if msgs else "无共同查询"


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="SF10 三轮配对实验分析（不覆盖旧结果）")
    ap.add_argument("--cl-dir", required=True, help="Directory containing newly collected paired runs")
    ap.add_argument("--out", default=None, help="输出前缀（默认 <cl-dir>/results-3rep/analysis）")
    args = ap.parse_args()
    out_prefix = args.out or os.path.join(args.cl_dir, "results-3rep", "analysis")
    os.makedirs(os.path.dirname(out_prefix), exist_ok=True)

    arch = os.path.join(args.cl_dir, "ldbc_snb_interactive_v1_impls-main/cypher/results-archive")
    mb_dir = os.path.join(args.cl_dir, "results-microbench-paired")
    ga_dir = os.path.join(args.cl_dir, "results-ga-analysis-paired")

    lines = ["# SF10 三轮配对实验分析报告（ratio = PET/Neo4j，<1 表示 PET 更快）", ""]
    payload = {}

    section_interactive(load_interactive(arch), lines, payload)
    mb = load_micro(mb_dir)
    if mb:
        section_micro(mb, mb_dir, lines, payload)
    else:
        lines.append("## 微基准：尚无 paired 结果（阶段未开始）")
    ga = load_ga(ga_dir)
    if ga:
        section_ga(ga, lines, payload)
    else:
        lines.append("## 图算法：尚无 paired 结果（阶段未开始）")
    an = load_analysis(ga_dir)
    if an:
        section_analysis(an, ga_dir, lines, payload)
    else:
        lines.append("## 分析查询：尚无 paired 结果（阶段未开始）")

    md = out_prefix + ".md"
    with open(md, "w") as f:
        f.write("\n".join(lines) + "\n")
    js = out_prefix + ".json"
    with open(js, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[done] {md}\n[done] {js}")


if __name__ == "__main__":
    main()
