#!/usr/bin/env python3
"""Plot the three paired SF10 runs, excluding the superseded unpaired runs.

Requires matplotlib. Run from any directory: python figures/plot_ldbc_sf10.py
Input: results-3rep/analysis.json, the full-precision export of analyze_3rep.py.
Every exported observation retains its source hash and JSON pointer.
Without the private archive: python figures/plot_ldbc_sf10.py --from-csv

Ratios are PET/Neo4j within each run; bars are geometric means of the three
ratios, whiskers their observed min/max (NOT confidence intervals). Request
samples within a run are not treated as independent experimental repetitions.
Median diamonds summarize ratios of within-run medians, not pooled medians.
Algorithm totals exclude initialization/cleanup. WCC reports both convergence
totals and the same first six iterations. No aggregate mixes reads and writes.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.transforms import Bbox

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "doc/neo4j-vs-petgraphdb-benchmark-v2/results-3rep/analysis.json"
OUT = ROOT / "figures/ldbc-sf10"
FIELDS = ["workload", "query", "rep", "statistic", "unit", "neo", "pet",
          "ratio", "neo_count", "pet_count", "source", "source_sha256",
          "neo_pointer", "pet_pointer"]


def geomean(values):
    values = list(values)
    assert values and all(v > 0 and math.isfinite(v) for v in values)
    return math.exp(sum(map(math.log, values)) / len(values))


def extract():
    data = json.loads(SOURCE.read_text())
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    rows = []

    def add(workload, query, rep, stat, unit, values, counts, pointers):
        neo, pet = values
        rows.append(dict(zip(FIELDS, [workload, query, rep, stat, unit, neo, pet,
                         pet / neo, *counts, str(SOURCE.relative_to(ROOT)),
                         digest, *pointers])))

    for rep in (1, 2, 3):
        for mode in ("ro", "rw"):
            keys = [f"{mode}-{rep}-{s}" for s in ("neo", "pet")]
            n, p = [data["interactive"][k] for k in keys]
            assert n.keys() == p.keys()
            assert len(n) == (21 if mode == "ro" else 29)
            for q in sorted(n):
                assert n[q]["count"] == p[q]["count"] > 0
                for stat, key in (("mean", "mean_ms"), ("p50", "p50_ms")):
                    add(f"interactive-{mode}", q, rep, stat, "ms",
                        (n[q][key], p[q][key]), (n[q]["count"], p[q]["count"]),
                        [f"/interactive/{k}/{q}/{key}" for k in keys])

        for kind in ("param", "agg"):
            keys = [f"{rep}-{s}-{kind}" for s in ("neo", "pet")]
            n, p = [data["microbench"][k] for k in keys]
            assert n.keys() == p.keys()
            assert len(n) == (17 if kind == "param" else 2)
            for q in sorted(n):
                assert n[q]["query"] == p[q]["query"]
                assert n[q]["count"] == p[q]["count"] == (100 if kind == "param" else 20)
                assert n[q]["errors"] == p[q]["errors"] == n[q]["timeouts"] == p[q]["timeouts"] == 0
                for stat, key in (("mean", "mean_ms"), ("p50", "median_ms")):
                    add("micro", q, rep, stat, "ms", (n[q][key], p[q][key]),
                        (n[q]["count"], p[q]["count"]),
                        [f"/microbench/{k}/{q}/{key}" for k in keys])

        keys = [f"{rep}-{s}" for s in ("neo", "pet")]
        n, p = [data["analysis"][k] for k in keys]
        assert n.keys() == p.keys() and len(n) == 5
        for q in sorted(n):
            assert n[q]["query"] == p[q]["query"]
            assert n[q]["count"] == p[q]["count"] == 100
            assert n[q]["empty_results"] == p[q]["empty_results"]
            assert n[q]["errors"] == p[q]["errors"] == n[q]["timeouts"] == p[q]["timeouts"] == 0
            for stat, key in (("mean", "mean_ms"), ("p50", "median_ms")):
                add("custom", q, rep, stat, "ms", (n[q][key], p[q][key]),
                    (n[q]["count"], p[q]["count"]),
                    [f"/analysis/{k}/{q}/{key}" for k in keys])

        n, p = [data["graphalgos"][k] for k in keys]
        for stat, key in (("mean", "mean_s"), ("p50", "median_s")):
            assert n["bfs"]["n_failed"] == p["bfs"]["n_failed"] == 0
            assert n["bfs"]["seeds"] == p["bfs"]["seeds"] == 50
            add("algorithm", "bfs", rep, stat, "s",
                (n["bfs"]["lat"][key], p["bfs"]["lat"][key]), (50, 50),
                [f"/graphalgos/{k}/bfs/lat/{key}" for k in keys])
        for algo in ("pagerank", "wcc"):
            a, b = n[algo], p[algo]
            count_key = "iters" if algo == "pagerank" else "iters_run"
            for v in (a, b):
                assert math.isclose(sum(v["per_iter_s"]), v["total_s"], rel_tol=1e-12)
            if algo == "pagerank":
                assert a["iters"] == b["iters"] == 20
                for x, y in zip(a["top3"], b["top3"]):
                    assert x["id"] == y["id"] and abs(x["pr"] - y["pr"]) <= 1e-6
            else:
                assert a["converged"] and b["converged"]
                assert a["components"] == b["components"] == 3275
                assert a["iters_run"] == 7 and b["iters_run"] == 6
            add("algorithm", algo, rep, "total", "s", (a["total_s"], b["total_s"]),
                (a[count_key], b[count_key]),
                [f"/graphalgos/{k}/{algo}/total_s" for k in keys])
        add("algorithm", "wcc", rep, "first6", "s",
            (sum(n["wcc"]["per_iter_s"][:6]), sum(p["wcc"]["per_iter_s"][:6])),
            (6, 6), [f"sum(/graphalgos/{k}/wcc/per_iter_s[0:6])" for k in keys])
    with OUT.with_suffix(".csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def plot(rows):
    assert all(math.isclose(r["ratio"], r["pet"] / r["neo"], rel_tol=1e-12) for r in rows)

    def ratios(workload, query, stat="mean"):
        selected = sorted([r for r in rows if r["workload"] == workload
                           and r["query"] == query and r["statistic"] == stat],
                          key=lambda r: int(r["rep"]))
        assert [int(r["rep"]) for r in selected] == [1, 2, 3]
        return [r["ratio"] for r in selected]

    # Match the preceding locking figure: Arial-compatible sans-serif plot
    # text, purple/orange series, light box borders and horizontal grid lines.
    # Panel captions belong to LaTeX subfigure, not the plotting canvas.
    plt.rcParams.update({"font.family": "sans-serif",
                         "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
                         "font.size": 6, "axes.labelsize": 6,
                         "xtick.labelsize": 5, "ytick.labelsize": 5.5,
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    # Equal category pitch in physical units, despite unequal category counts.
    # Explicit x limits below make each panel span exactly n categories.
    fig, axes = plt.subplots(1, 4, figsize=(7.1, 1.65), sharey=True,
                             gridspec_kw={"width_ratios": [21, 8, 19, 9]})

    def panel(ax, labels, triples, medians=None):
        x = np.arange(len(labels))
        values = np.array([geomean(v) for v in triples])
        lower = values - np.min(triples, axis=1)
        upper = np.max(triples, axis=1) - values
        ax.bar(x, values, width=.66, color="#4c4c70", label="Mean (GM)",
               yerr=np.maximum([lower, upper], 0), capsize=1.5,
               error_kw={"elinewidth": .65, "capthick": .65})
        if medians:
            mx = [i for i, v in enumerate(medians) if v is not None]
            ax.plot(mx, [geomean(medians[i]) for i in mx], "D", ms=2.8,
                    mfc="white", mec="#d57321", mew=.8, linestyle="none",
                    label="Median (GM)")
        ax.axhline(1, color="#555555", ls="--", lw=.6)
        ax.set_xlim(-.5, len(labels) - .5)
        ax.set_ylim(0, 2.05)
        ax.set_yticks(np.arange(0, 2.05, .5))
        ax.set_xticks(x, labels, rotation=60, ha="right", rotation_mode="anchor")
        ax.tick_params(axis="both", length=0, pad=2, colors="#262626")
        if ax is axes[0]:
            ax.set_ylabel("PETG / Neo4j time")
        for spine in ax.spines.values():
            spine.set_color("#bfbfbf")
            spine.set_linewidth(.5)
        ax.grid(axis="y", color="#d0d0d0", linewidth=.5)
        ax.set_axisbelow(True)

    reads = sorted({r["query"] for r in rows if r["workload"] == "interactive-ro"},
                   key=lambda q: (q.startswith("LdbcShort"),
                                  int(''.join(c for c in q if c.isdigit()))))
    labels = [("IS" if q.startswith("LdbcShort") else "IC")
              + ''.join(c for c in q if c.isdigit()) for q in reads]
    panel(axes[0], labels, [ratios("interactive-ro", q) for q in reads],
          [ratios("interactive-ro", q, "p50") for q in reads])
    updates = sorted({r["query"] for r in rows if r["workload"] == "interactive-rw"
                      and r["query"].startswith("LdbcUpdate")})
    panel(axes[1], [f"IU{i}" for i in range(1, 9)],
          [ratios("interactive-rw", q) for q in updates],
          [ratios("interactive-rw", q, "p50") for q in updates])

    probes = [
        ("point-by-id.sparse", "ID-S"), ("point-by-id.dense", "ID-D"),
        ("point-by-internal-id.na", "IID"),
        ("expand-1hop-out.sparse", "Out-S"), ("expand-1hop-out.dense", "Out-D"),
        ("expand-1hop-in.sparse", "In-S"), ("expand-1hop-in.dense", "In-D"),
        ("expand-2hop.sparse", "2H-S"), ("expand-2hop.dense", "2H-D"),
        ("khop-3-limited.sparse", "3H-S"), ("khop-3-limited.dense", "3H-D"),
        ("shortest-path.sparse", "SP-S"), ("shortest-path.dense", "SP-D"),
        ("write-set-prop.sparse", "Set-S"), ("write-set-prop.dense", "Set-D"),
        ("write-create-delete.sparse", "C/D-S"), ("write-create-delete.dense", "C/D-D"),
        ("agg-person-ip-prefix.na", "Agg-IP"), ("agg-message-year.na", "Agg-Y")]
    panel(axes[2], [v for _, v in probes],
          [ratios("micro", q) for q, _ in probes],
          [ratios("micro", q, "p50") for q, _ in probes])

    custom = sorted({r["query"] for r in rows if r["workload"] == "custom"})
    entries = [("algorithm", "bfs", "mean"), ("algorithm", "pagerank", "total"),
               ("algorithm", "wcc", "total"), ("algorithm", "wcc", "first6")]
    entries += [("custom", q, "mean") for q in custom]
    triples = [ratios(*e) for e in entries]
    medians = [ratios(w, q, "p50") if s == "mean" else None for w, q, s in entries]
    panel(axes[3], ["BFS", "PR", "WCC", "WCC-6", "Q1", "Q2", "Q3", "Q4", "Q5"],
          triples, medians)
    axes[3].axvline(3.5, color="gray", lw=.6)
    handles, labels = axes[0].get_legend_handles_labels()
    order = [labels.index("Mean (GM)"), labels.index("Median (GM)")]
    legend = axes[0].legend([handles[i] for i in order], [labels[i] for i in order],
                            loc="upper left", fontsize=5, frameon=True, fancybox=False,
                            framealpha=1, edgecolor="#bfbfbf", borderpad=.3,
                            labelspacing=.25, handlelength=1.5, borderaxespad=.4)
    legend.get_frame().set_linewidth(.4)
    fig.subplots_adjust(left=.075, right=.995, bottom=.20, top=.97, wspace=.16)
    fig.canvas.draw()
    # Verify the rendered bar widths agree, rather than only their data widths.
    widths = [ax.patches[0].get_window_extent().width for ax in axes]
    assert max(widths) - min(widths) < 1e-6, widths
    # Export caption-free panels by slicing the same canvas. All PDFs have the
    # same physical height, so equal includegraphics heights in LaTeX preserve
    # identical bar widths, font sizes, and aligned plot boxes across panels.
    positions = [ax.get_position() for ax in axes]
    boundaries = [0] + [(positions[i].x1 + positions[i + 1].x0) / 2
                         for i in range(3)] + [1]
    for i, letter in enumerate("abcd"):
        box = Bbox.from_extents(boundaries[i] * 7.1, 0,
                                boundaries[i + 1] * 7.1, 1.65)
        fig.savefig(OUT.with_name(f"ldbc-sf10-{letter}.pdf"), bbox_inches=box,
                    metadata={"CreationDate": None})
    fig.savefig(OUT.with_suffix(".pdf"), metadata={"CreationDate": None})
    fig.savefig("/tmp/opencode/ldbc-sf10-preview.png", dpi=200)
    plt.close(fig)

    groups = [("IC-ro", "interactive-ro", "LdbcQuery"),
              ("IS-ro", "interactive-ro", "LdbcShort"),
              ("IC-rw", "interactive-rw", "LdbcQuery"),
              ("IU-rw", "interactive-rw", "LdbcUpdate"),
              ("micro-read", "micro", ""), ("micro-write", "micro", "write-"),
              ("custom", "custom", "")]
    summaries = []
    for name, workload, prefix in groups:
        queries = sorted({r["query"] for r in rows if r["workload"] == workload
                   and r["query"].startswith(prefix)
                   and not (name == "micro-read" and r["query"].startswith("write-"))})
        for stat in ("mean", "p50"):
            values = [geomean(ratios(workload, q, stat)[i] for q in queries) for i in range(3)]
            summaries.append([name, stat, *values, geomean(values), min(values), max(values)])
    for w, q, s in entries + [("micro", "expand-2hop.dense", "mean"),
                              ("micro", "khop-3-limited.sparse", "mean")]:
        v = ratios(w, q, s)
        summaries.append([q, s, *v, geomean(v), min(v), max(v)])
    with OUT.with_name("ldbc-sf10-summary.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "statistic", "rep1", "rep2", "rep3", "geomean", "min", "max"])
        writer.writerows(summaries)
    for r in summaries:
        print(f"{r[0]:30s} {r[1]:6s}: " + " / ".join(f"{v:.6f}" for v in r[2:5])
              + f"; GM={r[5]:.6f}; range={r[6]:.6f}--{r[7]:.6f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", action="store_true")
    args = parser.parse_args()
    if args.from_csv:
        with OUT.with_suffix(".csv").open() as f:
            data = list(csv.DictReader(f))
        for row in data:
            for key in ("neo", "pet", "ratio"):
                row[key] = float(row[key])
    else:
        data = extract()
    plot(data)
