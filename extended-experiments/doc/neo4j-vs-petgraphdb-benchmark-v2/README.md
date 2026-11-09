# Impact of extending Neo4j: conventional workloads

All commands below assume the working directory is `extended-experiments/`.
Use `P=doc/neo4j-vs-petgraphdb-benchmark-v2` and the Python environment installed
by the top-level quick start.

## Archived results and paper mapping

`results-3rep/analysis.json` is the full-precision export from the three paired
SF10 runs, covering interactive RO/RW, 19 microbenchmarks, BFS/PageRank/WCC, and
five custom analytical queries. `analysis.md` is the original audit report.
Earlier unpaired, SF100, and HDD runs are deliberately not mixed into this data.
The paper uses:

* `figures/ldbc-sf10.csv`: 459 observations; columns identify workload, query,
  repetition, statistic, unit, both latencies, PET/Neo4j ratio, sample counts,
  source SHA-256, and JSON pointers.
* `figures/ldbc-sf10-summary.csv`: three-run group ratios and selected regressions.
* `figures/plot_ldbc_sf10.py`: extraction, aggregation, and all four panels.

Means are compared against means; medians are separate. Group aggregates are
geometric means, computed within each repetition before aggregation across the
three repetitions. Read/write groups are not pooled into one score. Algorithm
times exclude setup/cleanup. WCC reports convergence time and the same first six
iterations, since the engines converge in different iteration counts.

The archived analysis supports figure regeneration, not re-execution of the
original raw-data analysis: full SF10 per-request traces and snapshots were not
copied back from the experiment servers. The supplied analyzer accepts new runs
with the layout documented in its source header. Do not run it against the
archive and overwrite `results-3rep/analysis.json` with an empty/partial analysis.

## Environment and datasets

Historical server: PowerEdge T630, 2×Xeon E5-2640 v3 (16 physical cores/32 threads),
188 GiB RAM, NVMe. Client: i7-12700, 109 GiB RAM, Gigabit Ethernet. SF10 contains
about 30M vertices and 177M edges. Database heap was 48 GiB on both sides;
page cache used engine defaults, not an explicitly equal fixed value. Only one
database/workload was timed at once. There was no concurrent temporal workload.

Baseline: Neo4j Community **4.4.42**. PETGraphDB's image reported 4.4.37-SNAPSHOT,
but its kernel content largely tracks 4.4.48. Archived image IDs are in
`results/configs/image-digests-20261025.txt`; they are not full portable digests.
Use `neo4j:4.4.42` and a PETGraphDB build/image, recording full image digests and
`CALL dbms.components()` before rerunning. The older source modules in this
repository provide a build path, not a guarantee of binary identity to the
historical campaign. Version differences confound isolated extension-overhead
claims.

Generate the data with the **SNB Interactive v1 Hadoop** generator, not the
Interactive v2 or BI generator:

* Generator: https://github.com/ldbc/ldbc_snb_datagen_hadoop
* Cypher implementation/driver: https://github.com/ldbc/ldbc_snb_interactive_v1_impls
* Driver: https://github.com/ldbc/ldbc_snb_interactive_v1_driver

Use the included `scripts/params-sf10.ini` (CsvComposite serializers, long dates,
one thread and one update partition). Follow the generator's build/run guide with
this configuration as `params.ini`; its `ldbc/datagen` Docker image is an
alternative. Record the actual upstream revisions/image digests: the exact
historical generator/driver commits were not retained in this local archive.
Keep the resulting `social_network/` and `substitution_parameters/` together and
use identical generated inputs for both engines. Check the output has
`dynamic/person_0_0.csv`, `static/place_0_0.csv`, and update streams. A fresh
generation is not claimed to be byte-identical to the original server files.

## Import a fresh dataset

Convert headers into a **new** directory (no source files are modified):

```bash
P=doc/neo4j-vs-petgraphdb-benchmark-v2
mkdir -p work
.venv/bin/python scripts/prepare_ldbc.py /absolute/datagen/social_network work/converted-sf10
```

Import the same converted CSVs separately for each engine. The included
`scripts/pet-import.sh` uses only standard Neo4j 4.4 `neo4j-admin import` and also
works for the baseline image. Example for a **new empty** baseline directory:

```bash
mkdir work/neo-data
docker run --rm --entrypoint bash \
  -v "$PWD/work/neo-data:/data" \
  -v "$PWD/work/converted-sf10:/import:ro" \
  -v "$PWD/$P/scripts/pet-import.sh:/import-ldbc.sh:ro" \
  neo4j:4.4.42 /import-ldbc.sh
```

For PETGraphDB, mount a new `work/pet-root/data` at `/data` during import and use
its image instead. At runtime mount `work/pet-root` at `/database`. Do not mount
the baseline store into PETG or vice versa. Start each engine using its documented
entry point; set Neo4j 4.4's `NEO4J_dbms_memory_heap_initial__size=48g` and
`NEO4J_dbms_memory_heap_max__size=48g`, and PETG's `MAVEN_OPTS=-Xms48g -Xmx48g`.
Adjust for smaller machines, but disclose changes rather than comparing their
timings as the original configuration.

Run `scripts/indices.cypher` on **both** systems using cypher-shell/Bolt, then
`CALL db.awaitIndexes(3600)`. Check counts and that every index is ONLINE. Stop
the database cleanly and make an independent immutable baseline snapshot. Restore
that snapshot with the engine stopped before **each system × repetition × suite**;
never reuse a database changed by a previous RW run. Snapshot management is
deliberately manual, so no packaged script deletes or replaces user directories.

## Interactive workload

Build the upstream v1 Cypher implementation following its instructions (JDK 11,
Maven). Copy the four `driver-configs/*.properties` templates to ignored
`local-config/`; set endpoint, password, and absolute data/parameter paths.
Run from the upstream `cypher/` directory, where `queryDir=queries/` resolves.

Templates preserve the actual campaign settings: one thread, 5K warm-up and 100K
measured operations, time compression ratio 0.001. The inherited property
`ldbc.snb.interactive.scale_factor=1` is retained for provenance; the **data are
SF10**. Changing that setting is a protocol change, not a correction to archived
measurements. RO enables IC1–14 and IS1–7; RW additionally enables IU1–8.

Example (adjust the built JAR path if your upstream build differs):

```bash
java --add-exports=java.base/sun.nio.ch=ALL-UNNAMED \
  -cp target/cypher-1.2.0-SNAPSHOT.jar org.ldbcouncil.snb.driver.Client \
  -P /absolute/artifact/extended-experiments/local-config/bench-sf10-ro-neo.properties
```

For each rep 1–3, restore/start Neo4j, execute RO then RW, archive each driver's
`results/LDBC-SNB-results.json` **immediately** (the next run overwrites it), then
stop Neo4j; repeat with PETG. Use the same parameter/update streams. Archive as
`work/new-runs/ldbc_snb_interactive_v1_impls-main/cypher/results-archive/`
`sf10-{neo,pet}-{ro,rw}-rep{1,2,3}-results.json`. Also retain console logs, driver
validation outputs, runtime config, and image digests outside the committed archive.

## Operators and analytics

`results/params/params-sf10-frozen.json` provides the shared sparse/dense pools,
BFS seeds, and analytical-query parameters used by the campaign. It is not the
interactive driver's parameter stream. If your generated graph differs, validate
the IDs/counts first; optionally use `scripts/freeze_params.py --help` to create a
**new** shared parameter file from your data and pass it to every rerun.

Against a restored, disposable benchmark instance:

```bash
read -rs -p 'Database password: ' DB_PASSWORD
export DB_PASSWORD
.venv/bin/python scripts/run_operators.py --uri bolt://localhost:17688 \
  --system neo --rep 1 --suite micro --output work/new-runs --confirm-disposable
```

Repeat with `--system pet` and its endpoint, reps 1–3, and `--suite analytics`,
restoring each baseline before each leg. The wrapper uses the original workload
implementations and exact warm-up/repetition counts: microbenchmarks 20/100,
aggregations 5/20, BFS 50 seeds at depth 3, PageRank 20 iterations, WCC up to 50,
and custom queries 5/100. It refuses to overwrite existing run outputs. Timed
runs must be sequential with no competing DB container; do not parallelize engines.

After collecting all runs, analyze **new** results, not the immutable archive:

```bash
.venv/bin/python "$P/scripts/analyze_3rep.py" \
  --cl-dir "$PWD/work/new-runs" --out "$PWD/work/new-analysis"
```

Review missing data, errors/timeouts and pairing checks in the output. Do not
interpret partial runs as a full three-run result. Preserve request-level traces
from your campaign; they allow stronger verification than the archived export.
