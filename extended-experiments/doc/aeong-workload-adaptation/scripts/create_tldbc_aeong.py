# AeonG T-LDBC 造库驱动 v2（基于其 create_temporal_database.py）。
# v1 教训：此版本 AeonG 逐语句导入边约 2 条/s（每事务固定开销），20 worker 并发
# 更因热点顶点锁竞争重试恶化（2h 仅 2.2 万边）。v2 导入改用等价批量 UNWIND
# （ldbc_batch_convert.py，数据与原文件逐行等价，点数/边数断言一致）；
# 索引、DB 启动参数、操作流协议（20 worker 计时）与其原生 harness 完全一致。
import argparse
import sys
import time

sys.path.append('../mgbench')
import helpers
import runners


def get_space(folder_path):
    import os
    total = 0
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.endswith('.log') or f.endswith('.sst'):
                total += os.path.getsize(os.path.join(root, f))
    snap_dir = folder_path + "/memgraph/snapshots"
    snaps = os.listdir(snap_dir)
    if snaps:
        latest = max(snaps)
        total += os.path.getsize(os.path.join(snap_dir, latest))
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aeong-binary", default=helpers.get_binary_path("memgraph"))
    parser.add_argument("--client-binary", default=helpers.get_binary_path("tests/mgbench/client"))
    parser.add_argument("--num-workers", type=int, default=20)
    parser.add_argument("--import-workers", type=int, default=8)
    parser.add_argument("--data-directory", default="/work/db")
    parser.add_argument("--index-cypher-path", default="/work/data/ldbc_index.cypher")
    parser.add_argument("--vertices-path", default="/work/data/batched.vertices.cypher")
    parser.add_argument("--edges-path", default="/work/data/batched.edges.cypher")
    parser.add_argument("--graph-operation-cypher-path", default="/work/data/aeong_ops.txt")
    args = parser.parse_args()

    print("[phase0] start aeong (start_dataset flags)", flush=True)
    aeong = runners.Memgraph(args.aeong_binary, args.data_directory, True,
                             memgraph_port=7687, snapshot_interval_sec=30, memory_limit=0,
                             anchor_num=10, real_time_flag=False)
    aeong.start_dataset()
    client = runners.Client(args.client_binary, args.data_directory, memgraph_port=7687)

    t0 = time.time()
    client.execute(file_path=args.index_cypher_path, num_workers=1)
    print(f"[phase1] index done {time.time()-t0:.0f}s", flush=True)

    t0 = time.time()
    client.execute(file_path=args.vertices_path, num_workers=8)
    print(f"[phase2] vertices import done {time.time()-t0:.0f}s", flush=True)

    t0 = time.time()
    client.execute(file_path=args.edges_path, num_workers=args.import_workers)
    print(f"[phase3] edges import done {time.time()-t0:.0f}s", flush=True)

    start_time = int(time.time() * 1000000)
    graph_op_ret = client.execute(file_path=args.graph_operation_cypher_path,
                                  num_workers=args.num_workers)
    end_time = int(time.time() * 1000000)
    print(f"[phase4] graph ops done ret={graph_op_ret}", flush=True)
    time.sleep(60)
    aeong.stop()
    print(graph_op_ret[0]['duration'] / graph_op_ret[0]['count'],
          get_space(args.data_directory) / 1024 / 1024, start_time, end_time)
