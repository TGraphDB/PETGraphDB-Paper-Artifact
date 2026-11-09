#!/usr/bin/env python3
"""Plot archived T-mgBench/T-LDBC observations (requires matplotlib).

Run from any directory. --from-csv reproduces figures without the private doc/
archive. Bars compare arithmetic means, NOT AeonG means against PETG medians.
PETG means pool 5 steady executions per request; AeonG uses its second pass.
These are single campaigns with different clients/timelines/query projections,
not independent repetitions or strictly result-equivalent paired experiments.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "doc/aeong-workload-adaptation/results"
OUT = ROOT / "figures"
CSV = OUT / "aeong-workloads.csv"
FIELDS = ["workload", "query", "aeong_mean_ms", "petg_mean_ms",
          "petg_median_ms", "petg_p95_ms", "requests", "petg_steady_repeats",
          "aeong_source", "aeong_sha256", "aeong_pointer", "petg_source",
          "petg_sha256", "petg_pointer"]


def load(relative):
    path = SOURCE / relative
    text = path.read_text()
    # The mix-v1 file has failed Q2 log lines before its JSON report. Its Q2
    # entry is deliberately NOT used; the successful q2-v2 rerun replaces it.
    data = json.loads(text[text.index("{\n"):])
    return data, str(path.relative_to(ROOT)), hashlib.sha256(path.read_bytes()).hexdigest()


def extract():
    mg = load("aeong-side-2026-10-28/aeong-results.json")
    ld = load("tldbc-2026-10-28/tldbc-results.json")
    mix = load("server-run-2026-10-28/bench-mix-v1.json")
    q2 = load("server-run-2026-10-28/bench-q2-v2.json")
    extra = load("server-run-2026-10-28/bench-freq-int-v1.json")
    pet_ld = load("tldbc-2026-10-28/petgraph-bench-tldbc-v1.json")
    rows = []
    for workload, aeong, key, sources in [
        ("T-mgBench", mg, "query_results_avg_ms", [mix, q2, extra]),
        ("T-LDBC", ld, "aeong_query_results_avg_ms", [pet_ld]),
    ]:
        for query, mean in aeong[0][key].items():
            source = next(s for s in sources if query in s[0]["groups"]
                          and s[0]["groups"][query].get("failures") == 0)
            group = source[0]["groups"][query]
            assert group["queries"] == 100 and source[0]["steady"] == 5
            values = [1000 * group[f"steady_{stat}_s"] for stat in ("mean", "median", "p95")]
            assert mean > 0 and all(v > 0 for v in values)
            rows.append(dict(zip(FIELDS, [workload, query, mean, *values, 100, 5,
                             aeong[1], aeong[2], f"/{key}/{query}",
                             source[1], source[2], f"/groups/{query}"])))
    assert len(rows) == 20
    with CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def plot(rows):
    plt.rcParams.update({"font.family": "sans-serif",
                         "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
                         "font.size": 8, "axes.labelsize": 8,
                         "xtick.labelsize": 7, "ytick.labelsize": 7,
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    indexed = {(r["workload"], r["query"]): r for r in rows}
    assert len(indexed) == 20

    def values(workload, queries, system):
        return [float(indexed[workload, q][f"{system}_mean_ms"]) for q in queries]

    def style(ax):
        ax.set_ylabel("Mean latency (ms)")
        ax.grid(axis="y", color="#d0d0d0", linewidth=.5)
        ax.set_axisbelow(True)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_color("#bfbfbf")
            spine.set_linewidth(.5)

    colors = ["#4c4c70", "#d57321"]

    def bars(ax, workload, queries, labels, log=False):
        for i, system in enumerate(("aeong", "petg")):
            v = values(workload, queries, system)
            positions = [x + (i - .5) * .36 for x in range(len(queries))]
            ax.bar(positions, v, width=.34, color=colors[i], label=("AeonG" if i == 0 else "PETG"))
        ax.set_xticks(range(len(labels)), labels)
        if log:
            ax.set_yscale("log")
            ax.set_ylim(.8, 230)
            ax.set_yticks([1, 10, 100], ["1", "10", "100"])
            for i, q in enumerate(queries):
                a, p = [values(workload, [q], s)[0] for s in ("aeong", "petg")]
                ax.text(i, max(a, p) * 1.14, f"{a:.1f}/{p:.1f}", ha="center", fontsize=6)
        else:
            ax.set_ylim(0, 11.5)
        style(ax)

    fig, axes = plt.subplots(1, 3, figsize=(7.1, 2.15))
    bars(axes[0], "T-mgBench", [f"q{i}_mix" for i in range(1, 5)], [f"Q{i}" for i in range(1, 5)])
    bars(axes[1], "T-mgBench", [f"q3_{f}" for f in ("hot", "warm", "cold")], ["Hot", "Warm", "Cold"])
    intervals = [1, 20, 50, 100, 150, 200]
    for i, system in enumerate(("aeong", "petg")):
        axes[2].plot(intervals, values("T-mgBench", [f"q4_mix_int{n}" for n in intervals], system),
                     color=colors[i], marker=("o" if i == 0 else "s"), ms=3, lw=1,
                     label=("AeonG" if i == 0 else "PETG"))
    axes[2].set_ylim(0, 11.5)
    axes[2].set_xticks([1, 50, 100, 150, 200])
    axes[2].set_xlabel("Interval length (engine time units)")
    style(axes[2])
    axes[0].legend(loc="upper left", frameon=False, fontsize=7)
    fig.subplots_adjust(left=.065, right=.99, bottom=.22, top=.96, wspace=.36)
    # Export equal-width caption-free panels, for LaTeX-managed subcaptions.
    from matplotlib.transforms import Bbox
    for i, letter in enumerate("abc"):
        left = 0 if i == 0 else (axes[i-1].get_position().x1 + axes[i].get_position().x0) / 2
        right = 1 if i == 2 else (axes[i].get_position().x1 + axes[i+1].get_position().x0) / 2
        fig.savefig(OUT / f"aeong-mgbench-{letter}.pdf",
                    bbox_inches=Bbox.from_extents(left*7.1, 0, right*7.1, 2.15),
                    metadata={"CreationDate": None})
    fig.savefig(OUT / "aeong-mgbench.png", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(3.45, 2.15))
    bars(ax, "T-LDBC", [f"IS{i}" for i in range(1, 8)], [f"IS{i}" for i in range(1, 8)], log=True)
    ax.legend(loc="upper right", frameon=False, fontsize=7)
    fig.subplots_adjust(left=.15, right=.99, bottom=.14, top=.96)
    fig.savefig(OUT / "aeong-tldbc.pdf", metadata={"CreationDate": None})
    fig.savefig(OUT / "aeong-tldbc.png", dpi=200)
    plt.close(fig)

    print("AeonG mean / PETG steady mean (descriptive, not paired speedups):")
    for row in rows:
        a, p = float(row["aeong_mean_ms"]), float(row["petg_mean_ms"])
        print(f"{row['workload']:10s} {row['query']:16s} {a:8.3f} / {p:6.3f} ms; ratio {a/p:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-csv", action="store_true")
    args = parser.parse_args()
    if args.from_csv:
        with CSV.open() as f:
            rows = list(csv.DictReader(f))
    else:
        rows = extract()
    plot(rows)
