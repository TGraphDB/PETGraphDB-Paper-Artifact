# PETGraphDB

## Paper artifact and data availability

This repository contains the original PETGraphDB implementation and benchmarks,
plus the appendix's **extended experiments**:

* [Impact of extending Neo4j: LDBC SNB SF10](extended-experiments/doc/neo4j-vs-petgraphdb-benchmark-v2/README.md): interactive operations, operator microbenchmarks, graph algorithms, and analytical queries.
* [Generality: adapted T-mgBench and T-LDBC](extended-experiments/doc/aeong-workload-adaptation/README.md): workload adapters, frozen Pokec operations/queries, archived measurements, write costs, and storage footprints.

Start with [extended-experiments/README.md](extended-experiments/README.md) for
dependency installation, offline validation, figure regeneration, dataset acquisition,
and database rerun instructions. No paper checkout or laboratory server is needed
to validate the packaged measurements and regenerate the appendix plots.
Large datasets and database snapshots are not bundled; the documentation states
which inputs are included and which must be obtained separately. Original source
modules and their licenses are preserved below.

## Description

PETGraphDB is a data management system specifically designed for Property Evolution Temporal Graph (PETG) data. Built on top of Neo4j, it adopts a valid-time temporal property graph data model to facilitate intuitive data modeling while supporting ACID features with transactions. Unlike traditional systems that treat topological changes and property evolution identically, PETGraphDB addresses scenarios where property values change frequently while the graph topology remains relatively stable.

## Contributions

* **Native Valid-Time Support:** Provides native support for valid-time PETG data while preserving full ACID guarantees.


* **Space-Efficient Storage:** Introduces the Temporal Interval Merge Tree (TIM-Tree), a storage engine optimized specifically for entity-history queries and append-intensive workloads, effectively eliminating data redundancy.


* **High Transaction Throughput:** Features a fine-grained, multi-level locking mechanism designed to reduce unnecessary contention and maximize concurrent transaction throughput.


* **Backward Compatibility:** Fully backward compatible with the widely used property graph data model, enabling users to manage non-temporal Neo4j instances seamlessly.


* **TCypher Query Language:** Provides a high-level declarative query language extended from Cypher, minimizing the learning curve for manipulating temporal graphs.


## Getting Started: Reproducible Build Guide

This guide documents how to bootstrap from source and package all required modules into the local Maven repository.

### Source Repositories

* `temporal-storage`: The custom temporal storage engine module (TIM-Tree) used by PETGraphDB.
* `temporal-neo4j-4.4`: The PETGraphDB implementation built by modifying Neo4j 4.4, with `temporal-storage` as a core dependency.
* `demo-test`: The PETGraphDB benchmark and testing project, including dataset adapters, benchmark workloads, and automation scripts.

### Toolchain Requirements

* **Maven**: Maven (version between 3.8.1 and 3.9.9).
* **JDK**: Java 11 (Oracle/OpenJDK 11 recommended).

### Compile and Build

**1. Install the `temporal-storage` Module**
Run the following command at the root directory of `temporal-storage`:

```bash
mvn -B clean install -DskipTests
```

This step cleans, compiles, packages, and installs the module into the local Maven repository.

**2. Install the PETGraphDB Module**
Run the following command at the root directory of `temporal-neo4j-4.4`:

```bash
mvn -B clean install -DskipTests -Dcheckstyle.skip -Dlicense.skip=true -Dlicensing.skip=true -Doverwrite
```

The additional `-D` flags bypass Neo4j-specific style/license validations that are not required for local packaging.

**3. Install the demo-test Module**
Run the following command at the root directory of `demo-test`:

```bash
mvn -B clean compile -Dmaven.test.skip
```


## Benchmarks & Queries

The system supports testing against typical PETG data management scenarios using real-world and synthetic datasets (Energy, Traffic, and SYN).

It natively supports the execution of complex temporal operations for temporal graphs, including:

* **Read Operations:** Entity-History, Snapshot, Graph Aggregate Temporal Property (GATP), Entity Temporal Property Condition (ETPC) queries and Traversal Queries.
* **Write Operations:** Update and Append.

## PETGraphDB Implementation

PETGraphDB consists of two main components ( Details of our concept can be found in our paper ): the query language processor and the database kernel. The database kernel provides comprehensive temporal graph data management and transactional capabilities accessible through Java APIs. The query language processor handles TCypher queries , accessing the database kernel for execution.

* **Storage Engine (TIM-Tree):** Data items are partitioned and ordered first by property, and subsequently by time, effectively addressing data access skew. The engine employs an "export/merge" batch write process, buffering updates in a Global Memtable before periodically merging them with on-disk SSTables.


* **Redundancy Reduction:** For continuously changing properties, the system omits the end time of intervals, deriving it from subsequent data items, and applies Snappy prefix compression to disk files.


* **Transaction Management:** Neo4j's transaction pipelines are refactored to support temporal data structures in the memory buffer before committing, ensuring Atomicity, Consistency, Isolation, and Durability.


* **Locking Mechanism:** Extends Neo4j's entity-level locks with temporal-level shared and exclusive locks, allowing transactions that access the same entity but disjoint time ranges to execute concurrently. Deadlocks are managed using a wait-for graph detection approach.

## Configure IntelliJ IDEA (Java and Scala)**

* **Set project JDK:** `File -> Settings -> Project Structure -> Project -> SDK = Java 11`.
* **Configure Scala SDK 2.12.13:** Install the Scala plugin from the IDEA marketplace, restart, and configure the global library (`File -> Settings -> Project Structure -> Global Library -> + -> Scala SDK -> 2.12.13`).
* **Import modules** Import `temporal-storage` and `temporal-neo4j-4.4` as an IDEA module to enable direct source-level debugging across the storage engine and PETGraphDB code.
