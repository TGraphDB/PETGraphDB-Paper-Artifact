#!/usr/bin/env python3
"""PETGraphDB 语句执行驱动（客户端）：N 工作线程并发执行语句文件，统计时延。

复刻 AeonG mgbench client 的口径：每行一条语句、自动提交、共享计数器每行恰执行一次；
duration/count ≈ 单语句平均延迟。失败语句隔离记录（记 lineno+error），最后汇总报告。

用法: petgraph_driver.py --bolt bolt://server:17687 --file ops.cypher --workers 20
"""
import argparse
import faulthandler
import json
import logging
import os
import threading
import time

from neo4j import GraphDatabase

logging.getLogger("neo4j").setLevel(logging.CRITICAL)
faulthandler.enable()


class Shared:
    def __init__(self, lines):
        self.lines = lines
        self.next_i = 0
        self.lock = threading.Lock()
        self.failures = []
        self.done = 0
        self.progress_path = os.environ.get("DRIVER_PROGRESS", "")

    def mark(self):
        with self.lock:
            self.done += 1
            if self.progress_path and self.done % 10000 == 0:
                with open(self.progress_path, "w") as f:
                    f.write(f"{self.done}/{len(self.lines)}\n")

    def pull(self):
        with self.lock:
            if self.next_i >= len(self.lines):
                return None, None
            i = self.next_i
            self.next_i += 1
            return i, self.lines[i]


def worker(uri, password, shared, stats):
    drv = GraphDatabase.driver(uri, auth=("neo4j", password))
    local_done = 0
    t0 = time.perf_counter()
    with drv.session() as s:
        while True:
            i, stmt = shared.pull()
            if stmt is None:
                break
            try:
                s.run(stmt).consume()
                local_done += 1
                shared.mark()
            except Exception as e:  # noqa: BLE001
                shared.lock.acquire()
                shared.failures.append((i, f"{type(e).__name__}: {str(e)[:200]}"))
                shared.lock.release()
    stats["wall"] = time.perf_counter() - t0
    stats["done"] = local_done
    drv.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolt", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--tag", default="run")
    args = ap.parse_args()

    with open(args.file) as f:
        lines = [l.strip() for l in f if l.strip()]

    shared = Shared(lines)
    stats = [{} for _ in range(args.workers)]
    threads = [threading.Thread(target=worker, args=(args.bolt, args.password, shared, stats[i]))
               for i in range(args.workers)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - wall_start

    done = sum(s.get("done", 0) for s in stats)
    report = {
        "tag": args.tag,
        "file": args.file,
        "workers": args.workers,
        "statements_total": len(lines),
        "statements_ok": done,
        "failures": len(shared.failures),
        "failure_samples": shared.failures[:5],
        "wall_seconds": round(wall, 3),
        "avg_latency_seconds": round(wall / len(lines), 6) if lines else None,
        "throughput_ops_per_s": round(done / wall, 1) if wall > 0 else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
