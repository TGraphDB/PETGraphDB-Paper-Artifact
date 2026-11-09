# Generality: adapted T-mgBench and T-LDBC

All commands assume `extended-experiments/` as the working directory, with
`P=doc/aeong-workload-adaptation`. Use a fresh **disposable** database for each
workload. Import/temporalization/update commands modify database state.

## Archived evidence and semantics

The paper's four temporal-query panels use arithmetic means from
`figures/aeong-workloads.csv` (20 groups), extracted from the reports under
`results/`. CSV rows retain source SHA-256 and JSON pointers, with PETG median/p95
as supplemental columns. AeonG uses 100 requests/group on its second pass; PETG
uses one warm-up and five steady executions/request. The failed Q2 group in
`server-run-2026-10-28/bench-mix-v1.json` is **not** used: successful
`bench-q2-v2.json` replaces it. Older reports contain median comparisons and
rounded MB values; the CSVs, not those older annotations, define paper figures.

AeonG uses transaction-time graph versions. PETG represents entity lifetimes
with temporal integer `valid` (1/0), retaining physical topology, and stores
evolving properties as temporal values. PETG operations use logical indices;
AeonG queries sample its own observed commit window. Returns differ: properties
or endpoint IDs versus historical entities. Q4 checks edge and neighbor overlap
**separately**, not common-time historical validity. T-LDBC IS3 needs no temporal
read. The adapted requests are not guaranteed to return equivalent histories;
engine/client differences also prevent isolated architectural speedup claims.

### Storage and write-cost table

`figures/aeong-workload-storage.csv` records exact bytes and provenance:

* T-mgBench PETG: **14,352,636 bytes = 13.69 MiB** (2026-10-30 measurement of
  `data/databases/neo4j`, including temporal data); AeonG: **65,008,435 bytes =
  62.00 MiB**, from `results/aeong-side-2026-10-28/aeong-create.log`.
* T-LDBC PETG: **207,638,318 bytes = 198.02 MiB**; AeonG: **321,292,210 bytes =
  306.41 MiB**, with provenance in `results/tldbc-2026-10-28/`.
* PETG T-LDBC baseline was 178,929,664 bytes. Temporalization **plus updates**
  adds 28,708,654 bytes, not exclusively historical-value growth.

Count database data only, excluding audit, transaction/WAL, operational logs,
and the internal system database. For AeonG, count the latest snapshot plus
retained history deltas. RocksDB history `.log` files contain data and must not
be discarded as operational logs. Measure after a clean stop/checkpoint and
retain exact byte counts; do not use whole-volume `du` as the paper metric.

Write times are duration/operation-count at **20 workers**, not individual-request
latencies: AeonG/PETG = 0.042/1.144 ms for T-mgBench and 0.043/0.971 ms for
T-LDBC. Raw reports retain higher-precision timings where available. These are
single campaigns, not independently replicated temporal experiments.

## System setup

Historical server: T630, NVMe, 188 GiB RAM, 32 hardware threads. PETG image ID
prefix `3cf7a71c73d7`, AeonG `afb858534be6`; these abbreviated IDs are not pullable
digests. PETG query image had the cache-validation patch (prefix `aa2a58dcc85b`).
Use the original source modules' build instructions or resolve available images
and record their full digests. Mutable `latest` tags may have changed.

AeonG source used in the campaign: https://github.com/songjinghe/AeonG at commit
prefix `1f9d946`; original upstream: https://github.com/hououou/AeonG. Build its
Memgraph executable and `tests/mgbench/client` following that repository's guide.
The C++ Bolt client is used for AeonG, not the Python PETG client.

Before querying PETG with small caches, build the included cache-patch recipe:

```bash
docker build -f docker/Dockerfile.query-cache -t petgraphdb:query-cache .
```

It changes only the incorrect cache-size lower-bound validation. Start the query
container with `CONFIG_MEMTABLE_SIZE=32`, `CONFIG_FBUFFER_SIZE=16`,
`CONFIG_MAX_CACHE_SIZE=256`, and
`MAVEN_OPTS=-Xmx3g -Xms1g -XX:MaxDirectMemorySize=2g`. Mount the imported PETG root
at `/database`, expose its Bolt port 17687, and **verify logs show effective
32/16/256 MiB**, not a silent 3072 MiB fallback. Import used larger settings:
64/32/1024 requested; the historical unpatched import image actually fell back
to 3072 MiB cache. Do not assume requested values took effect.

Use a dedicated machine for cold-start discipline: stop DB, sync and drop OS
caches with administrator approval, start query DB, wait for readiness, then
execute the warm-up/steady protocol. Cache dropping is machine-wide and is not
automatically done by packaged scripts.

## T-mgBench: inputs and PETGraphDB execution

Obtain Pokec small separately, preserving upstream dataset terms:

```bash
P=doc/aeong-workload-adaptation
mkdir -p work/pokec
curl --fail --location \
  https://s3.eu-west-1.amazonaws.com/deps.memgraph.io/dataset/pokec/benchmark/pokec_small_import.cypher \
  --output work/pokec/small.cypher
gzip -dc "$P/data/results/graph_op/petgraph_ops.txt.gz" > work/pokec/petgraph_ops.txt
gzip -dc "$P/data/results/graph_op/cypher.txt.gz" > work/pokec/aeong_ops.txt
read -rs -p 'PETGraphDB password: ' PET_PASSWORD
export PET_PASSWORD
.venv/bin/python "$P/scripts/import_dataset.py" --bolt bolt://localhost:17687 \
  --password "$PET_PASSWORD" --dataset work/pokec/small.cypher > work/pokec/import.json
```

Expected baseline: **10,000 User vertices, 121,716 Friend edges**, with `valid`
on all entities and temporal `completion_percentage` initialized on users.
The compressed files contain the frozen 320K operations; no new random operation
generation is needed. `scripts/rewrite_op_stream.py` can independently reproduce
the PETG stream from the AeonG one. `scripts/reconstruct_aeong_ops.py` implements
the inverse and verifies the forward round trip.

```bash
.venv/bin/python "$P/scripts/petgraph_driver.py" --bolt bolt://localhost:17687 \
  --password "$PET_PASSWORD" --file work/pokec/petgraph_ops.txt --workers 20 \
  --tag pokec-writes > work/pokec/writes.json
```

Inspect failure counts. Concurrency can cause create-edge operations to match no
rows before endpoints exist; zero query errors do not establish that every
requested mutation changed a row. This behavior is part of the archived protocol.
After import/updates, stop the DB and restart with the query-cache configuration.

```bash
.venv/bin/python "$P/scripts/run_benchmark.py" --bolt bolt://localhost:17687 \
  --password "$PET_PASSWORD" --query-dir "$P/data/results/temporal_query" \
  --patterns q1_mix,q2_mix,q3_mix,q4_mix,q3_hot,q3_warm,q3_cold,q4_mix_int \
  --steady 5 --tag pokec-queries > work/pokec/queries.json
```

The 22 frozen PETG files include Q1–4 across frequency classes and six Q4 interval
lengths 1/20/50/100/150/200. Only 13 groups are plotted. The manifest includes
SHA-256 for all files. Never replace them in place with a new generation.

The AeonG counterparts are packaged under `data/aeong-temporal-query/`. They were
regenerated from the frozen PETG files and archived time window, and **all 22
hashes match the original campaign's archived AeonG manifest**. Use those requests
only with the original history/time window; regenerate for a newly built DB.

## T-mgBench: AeonG execution

Use AeonG's `tests/scripts/create_temporal_database.py` with absolute paths:
`--binary-type aeong --benchmark-type mgbench --num-workers 20 --anchor-num 10`,
`--aeong-binary <build/memgraph>`, `--client-binary <build/tests/mgbench/client>`,
`--data-directory <new-db>`, `--original-dataset-cypher-path <small.cypher>`,
`--index-cypher-path <cypher_index.cypher>`, and
`--graph-operation-cypher-path <work/pokec/aeong_ops.txt>`.
The index file is supplied under `data/`. Run the harness from its upstream
`tests/scripts` directory so its relative Python imports work.

Effective AeonG flags: properties on edges, memory limit 0, anchor 10, GC cycle
30s, real-time false, recovery enabled; construction snapshots every 30s and on
exit. No retention policy is enabled (all history retained). Query runs recover
the constructed store with the same flags; no snapshot-on-exit is needed.

Determine the **new run's actual update commit window**, not the final snapshot
control timestamp. Generate queries retaining the same vertex IDs but sampling
AeonG time independently:

```bash
.venv/bin/python "$P/scripts/gen_aeong_queries.py" \
  --src-dir "$P/data/results/temporal_query" --out-dir work/pokec/aeong-queries \
  --ts-lo 264242 --ts-hi 958716 --seed 42
```

Those example bounds reproduce the **archived** request manifest; replace them
with observed bounds for a fresh DB. Evaluate each group with upstream
`evaluate_temporal_q.py --help`, supplying its AeonG/C++ binaries, database path,
dataset/index paths, temporal query file, `--binary-type aeong`, and one worker.
The native evaluator takes two passes; report the second pass's arithmetic mean.
Do not use the PETG five-repeat driver for AeonG and call it the original protocol.

## T-LDBC: preparing a fresh campaign

The original server-only 1.5M operation files and IS request files are not bundled.
The generator, adapters, seven query templates, archived measurements and query
hash manifests are included. Regeneration enables a new campaign, not guaranteed
byte-for-byte replay of the original run.

Acquire the **matching** SNB SF0.1 baseline (327,588 vertices, 1,477,965 edges) and
CsvBasic files used by AeonG. Original acquisition locations:

* `https://s3.eu-west-1.amazonaws.com/deps.memgraph.io/dataset/ldbc/benchmark/interactive/ldbc_interactive_sf0.1.cypher.gz`
* `https://repository.surfsara.nl/datasets/cwi/snb/files/social_network-csv_basic/social_network-csv_basic-sf0.1.tar.zst`

If unavailable, consult AeonG's `tests/experiments/t_ldbc_dataset.sh` or regenerate
matching data with the upstream SNB generator, documenting any changed inputs.
Do not substitute SF10 or mismatched IDs/labels. For PETG baseline import use the
conventional experiment's `headers.txt`, import recipe and indexes with matching
CsvComposite inputs, or an equivalent Cypher import; validate counts and labels.

With CsvBasic files extracted and a fresh work directory:

```bash
mkdir -p work/tldbc/graph_op work/tldbc/peak_vertices
.venv/bin/python "$P/scripts/ldbc_op_gen_fixed.py" --size sf0.1 --num-op 1000000 \
  --update-ratio 1 --delete-ratio 0 --write-path "$PWD/work/tldbc/" \
  --ldbc-dataset-path /absolute/csv-basic/
.venv/bin/python "$P/scripts/rewrite_ldbc_ops.py" \
  work/tldbc/graph_op/cypher.txt work/tldbc/petgraph_ops.txt
.venv/bin/python "$P/scripts/temporalize_ldbc.py" --bolt bolt://localhost:17687 \
  --password "$PET_PASSWORD"
.venv/bin/python "$P/scripts/petgraph_driver.py" --bolt bolt://localhost:17687 \
  --password "$PET_PASSWORD" --file work/tldbc/petgraph_ops.txt --workers 20 \
  --tag tldbc-writes > work/tldbc/writes.json
.venv/bin/python "$P/scripts/gen_ldbc_queries.py" --format petgraph \
  --peak-dir work/tldbc/peak_vertices --out-dir work/tldbc/pet-queries \
  --time-min 1 --time-max 1500000 --num-op 1000000 --per-is 100 --seed 42
```

`--num-op 1000000` yields **1.5M statements**, because each selected Person gets
two property updates: locationIP/browserUsed 500K each; Comment/Post content 250K
each. Seeds are 42. The fixed generator removes quoted integer IDs; create/delete
paths are not supported by this experiment. Temporalization first removes static
versions of the four properties before writing their baseline values via `tp.set`.
Run PETG queries with `run_benchmark.py --patterns IS`, `--steady 5`, and the
restarted query container, supplying `--bolt`, `--password`, and
`--query-dir work/tldbc/pet-queries` as in the Pokec example.

For AeonG, convert the original SF0.1 Cypher import using
`ldbc_batch_convert.py <ldbc.cypher> <prefix> --batch 2000 --shuffle-edges`.
It asserts the expected vertex/edge counts. Create indexes on every endpoint label
(Place, City, Country, Continent, Organisation, Company, University, TagClass,
Tag, Comment, Post, Person, Forum). Place `create_tldbc_aeong.py` in AeonG's
`tests/scripts/` and run it there, supplying binaries, new database directory,
index file, batched vertex/edge files, and `graph_op/cypher.txt` as explicit paths.
It uses 20 operation workers and the same native construction flags. Generate IS
queries with `gen_ldbc_queries.py --format aeong` using the **same** peak files
and seed but the new commit window (archived window 1010000–1350000), then use
the native second-pass, one-worker evaluator. Record hashes and errors before
comparing arithmetic means, writes, and storage under the definitions above.
