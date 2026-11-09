# Extended experiments for the PETGraphDB paper

This is a self-contained supplement to the implementation in the repository's
three original source modules. It covers the two **Extended Experiments** in the
appendix, identified by topic rather than figure numbers (which may change).

## Quick start: validate and regenerate the appendix figures

From this directory, using Python **3.11 or 3.12** (with its venv/ensurepip support):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PYTHON=.venv/bin/python bash reproduce.sh
```

Alternatively, with `uv` installed: `uv venv --python 3.12 .venv`, then
`uv pip install --python .venv/bin/python -r requirements.txt`.

The command makes no database connections. It verifies packaged files and source
hashes, checks all 459 conventional-workload observations and 20 temporal query
groups against their archived JSON values, checks storage unit conversions and
the frozen Pokec inputs, then creates:

* `figures/ldbc-sf10-{a,b,c,d}.pdf`: conventional interactive reads, updates,
  microbenchmarks, and analytics.
* `figures/aeong-mgbench-{a,b,c}.pdf` and `figures/aeong-tldbc.pdf`: the four panels
  combined into one row in the paper.
* Additional overview PDF/PNG files produced by the plotting scripts.

Validation alone needs only Python's standard library:

```bash
python3 scripts/verify.py
```

To re-extract plotting CSVs directly from the included reports, omit `--from-csv`:

```bash
.venv/bin/python figures/plot_ldbc_sf10.py
.venv/bin/python figures/plot_aeong_workloads.py
```

Both modes operate entirely within this directory, independent of the paper's
private working tree. The `doc/` layout is retained to keep CSV provenance paths
and archived source hashes valid. For CSV schemas and interpretation, see the
two experiment guides below.

## Experiment guides

* [Neo4j extension impact / LDBC SNB SF10](doc/neo4j-vs-petgraphdb-benchmark-v2/README.md)
* [Generality / AeonG T-mgBench and T-LDBC](doc/aeong-workload-adaptation/README.md)

Each guide separates archived-result reproduction from a fresh database campaign.
Database reruns require Docker or source builds, JDK 11, Maven 3.8.1–3.9.9, and
the separately obtained datasets. They are resource-intensive and are **not**
launched by `reproduce.sh`. Only use disposable benchmark databases: import,
updates, temporalization, and graph algorithms all modify database contents.

## What is available

Included: workload adapters and query implementations; SF10 frozen parameters;
interactive-driver configuration templates and LDBC data-generator settings;
compressed, frozen 320K Pokec operation streams and 22 query files per engine;
the archived reports used by the paper; plotting CSVs with provenance; plotting
and validation scripts; exact storage measurements; the query-cache patch recipe.

Not included: database snapshots, full Pokec/LDBC source datasets, Docker layers,
third-party engine source trees, complete SF10 per-request logs, or the original
T-LDBC operation/query files. T-LDBC generators and query manifests are provided,
but a new input generation is **not claimed to be byte-identical** to the original
server-only campaign. SF10's full-precision `results-3rep/analysis.json` is the
archived analysis export, not a substitute for missing raw request traces.
`analyze_3rep.py` is supplied for analyzing **newly collected** raw runs.

The manifest records SHA-256 for packaged experiment assets and the decompressed
Pokec streams. Historical reports retain their original environment labels and
non-public LAN addresses as provenance; no script requires those machines.
Executable scripts require user-supplied credentials. Driver templates contain
placeholders; keep edited credentials in ignored `local-config/`.

## Interpretation and reproducibility limits

* SF10 bars are geometric means of three within-run PETGraphDB/Neo4j arithmetic
  mean latency ratios; diamonds use within-run medians. Whiskers are the observed
  three-run min/max, **not** confidence intervals. Baseline Neo4j is 4.4.42; PETG's
  runtime version label is 4.4.37-SNAPSHOT, with kernel content largely aligned
  with 4.4.48. This is an end-to-end comparison, not isolated extension overhead.
* Temporal plots compare arithmetic means on both sides. PETG pools five steady
  executions after one warm-up per request; AeonG uses its second pass. Different
  clients, engines, time axes, projections, and interval semantics mean these are
  adapted-workload observations, not query-equivalent speedups.
* Storage uses exact bytes / 2^20 (MiB). Database data and retained historical
  deltas count; audit, transaction/WAL, and operational logs do not. In particular,
  RocksDB files under AeonG's `history_deltas`, including `.log` files containing
  retained history, count as data rather than being excluded by extension.
* Hardware and immutable image identification affect rerun timing. Historical
  abbreviated image IDs are provenance, not portable pullable digests. Resolve
  and record a complete digest before a new campaign; mutable tags alone do not
  recover the original environment. No exact timing or binary identity is promised
  for a fresh build of the older artifact source tree.

## Third-party materials

The original implementation modules retain their own license files. Do not infer
a new license for Neo4j, AeonG, LDBC, Memgraph datasets, or other third-party work.
The T-LDBC operation generator is adapted from AeonG's benchmark generator, as
noted in its source header. Obtain upstream software/data under their respective
licenses and cite the originating benchmarks. Dataset links and acquisition
instructions are in the experiment guides; large datasets are not redistributed.
