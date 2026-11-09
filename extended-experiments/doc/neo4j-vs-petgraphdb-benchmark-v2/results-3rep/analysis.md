# SF10 三轮配对实验分析报告（ratio = PET/Neo4j，<1 表示 PET 更快）

## Interactive（单线程 100k op，rep1 为既有归档 + rep2/3 新跑）
每轮四列：neo mean(ms) / pet mean(ms) / mean ratio / median ratio；ratio = PET/Neo4j（读为 ro 模式、更新为 rw 模式）。
```
query                         class       r1 neo    r1 pet  r1 r_mr1 r_p50    r2 neo    r2 pet  r2 r_mr2 r_p50    r3 neo    r3 pet  r3 r_mr3 r_p50
LdbcQuery1                    cxread       811.0     666.4   0.822   0.789     812.7     627.1   0.772   0.767     710.9     661.7   0.931   0.934
LdbcQuery10                   cxread      1712.0    1136.8   0.664   0.635    1710.6    1050.1   0.614   0.597    1437.2    1085.0   0.755   0.761
LdbcQuery11                   cxread        78.4      53.5   0.683   0.667      78.7      50.1   0.637   0.627      66.1      53.3   0.807   0.794
LdbcQuery12                   cxread       726.9     456.2   0.628   0.619     720.7     410.2   0.569   0.562     626.4     441.1   0.704   0.725
LdbcQuery13                   cxread         3.3       1.9   0.568   0.667       3.3       1.8   0.540   0.667       3.2       1.8   0.578   0.667
LdbcQuery14                   cxread      8420.7    7246.3   0.861   0.819    8367.1    6734.0   0.805   0.763    6743.4    7078.7   1.050   1.033
LdbcQuery2                    cxread       187.2      82.3   0.440   0.417     188.7      77.7   0.412   0.411     166.2      86.3   0.519   0.500
LdbcQuery3                    cxread      4579.0    1894.9   0.414   0.407    4614.1    1776.6   0.385   0.373    4114.1    1898.6   0.461   0.464
LdbcQuery4                    cxread       134.0      81.6   0.609   0.585     135.0      72.9   0.540   0.529     111.7      78.6   0.704   0.697
LdbcQuery5                    cxread      3428.0    1846.0   0.539   0.558    3448.4    1684.4   0.488   0.527    2988.1    1823.3   0.610   0.590
LdbcQuery6                    cxread     10501.2    6336.6   0.603   0.574   10311.9    6005.1   0.582   0.561    8710.9    6339.7   0.728   0.721
LdbcQuery7                    cxread         1.7       1.4   0.804   1.000       2.1       1.3   0.617   0.500       1.6       1.4   0.880   1.000
LdbcQuery8                    cxread         1.3       1.1   0.815   1.000       1.7       1.1   0.626   0.500       1.3       1.1   0.877   1.000
LdbcQuery9                    cxread     19979.0    8652.6   0.433   0.419   19833.7    8473.8   0.427   0.415   17706.9    9333.0   0.527   0.521
LdbcShortQuery1PersonProfile  short          1.4       1.3   0.969   1.000       1.4       1.3   0.933   1.000       1.3       1.3   0.991   1.000
LdbcShortQuery2PersonPosts    short          7.2       3.4   0.471   0.500       7.8       3.2   0.405   0.400       6.7       3.5   0.519   0.500
LdbcShortQuery3PersonFriends  short          3.1       2.2   0.720   1.000       3.1       2.1   0.680   1.000       2.9       2.3   0.774   1.000
LdbcShortQuery4MessageContent short          1.4       1.3   0.923   1.000       1.5       1.3   0.860   1.000       1.4       1.3   0.932   1.000
LdbcShortQuery5MessageCreator short          1.2       1.1   0.939   1.000       1.2       1.1   0.928   1.000       1.2       1.1   0.959   1.000
LdbcShortQuery6MessageForum   short          1.1       1.0   0.947   1.000       1.1       1.0   0.948   1.000       1.1       1.0   0.973   1.000
LdbcShortQuery7MessageReplies short          1.3       1.1   0.839   1.000       1.3       1.1   0.795   1.000       1.3       1.1   0.872   1.000
LdbcUpdate1AddPerson          update         6.2       6.1   0.980   0.833       6.3       5.8   0.921   1.000       6.0       6.5   1.091   1.000
LdbcUpdate2AddPostLike        update         2.0       2.0   1.009   1.000       2.3       1.9   0.813   1.000       1.9       2.2   1.148   1.000
LdbcUpdate3AddCommentLike     update         2.0       2.0   1.025   1.000       2.3       1.9   0.827   1.000       1.9       2.2   1.166   1.000
LdbcUpdate4AddForum           update         2.2       2.3   1.034   1.000       2.6       2.1   0.807   1.000       2.1       2.5   1.188   1.000
LdbcUpdate5AddForumMembership update         1.9       2.0   1.019   1.000       2.3       1.9   0.823   1.000       1.9       2.1   1.129   1.000
LdbcUpdate6AddPost            update         2.5       2.6   1.041   1.500       2.8       2.4   0.847   0.667       2.4       2.6   1.077   1.000
LdbcUpdate7AddComment         update         2.6       2.7   1.014   1.000       3.0       2.5   0.842   0.667       2.5       2.7   1.086   0.667
LdbcUpdate8AddFriendship      update         1.9       2.0   1.029   1.000       2.2       1.9   0.848   1.000       1.9       2.1   1.154   1.000
```

分组几何平均（ratio，跨轮 min–max）：
- cxread: r1=0.617, r2=0.560, r3=0.704  | min=0.560 max=0.704
- short: r1=0.809, r2=0.766, r3=0.843  | min=0.766 max=0.843
- update: r1=1.019, r2=0.840, r3=1.129  | min=0.840 max=1.129

## 微基准（19 探针：参数化 9 探针×稀/稠 + 聚合 2 + 写 2；warmup 20+reps 100，聚合 5+20）
ratio = PET mean / Neo mean；同时给出 median ratio。
```
probe.tier                          r mean-ratio r med-ratio      r1      r2      r3  mean-ratio min–max
agg-message-year.na                        1.124       1.131   1.124   1.046   0.880  0.880–1.124
agg-person-ip-prefix.na                    0.940       0.944   0.940   1.010   1.024  0.940–1.024
expand-1hop-in.dense                       1.044       1.036   1.044   0.973   0.950  0.950–1.044
expand-1hop-in.sparse                      1.046       1.012   1.046   0.770   0.804  0.770–1.046
expand-1hop-out.dense                      0.997       1.000   0.997   0.928   0.897  0.897–0.997
expand-1hop-out.sparse                     1.089       1.046   1.089   0.761   1.060  0.761–1.089
expand-2hop.dense                          1.497       1.436   1.497   1.277   1.793  1.277–1.793
expand-2hop.sparse                         1.072       1.064   1.072   1.145   0.990  0.990–1.145
khop-3-limited.dense                       0.965       1.002   0.965   1.019   1.090  0.965–1.090
khop-3-limited.sparse                      1.197       1.162   1.197   1.368   1.142  1.142–1.368
point-by-id.dense                          0.422       0.665   0.422   0.592   0.780  0.422–0.780
point-by-id.sparse                         0.761       0.757   0.761   0.630   0.789  0.630–0.789
point-by-internal-id.na                    0.528       0.558   0.528   0.789   0.813  0.528–0.813
shortest-path.dense                        0.980       1.028   0.980   0.965   1.026  0.965–1.026
shortest-path.sparse                       1.470       1.409   1.470   0.961   1.032  0.961–1.470
write-create-delete.dense                  1.003       0.997   1.003   1.054   1.126  1.003–1.126
write-create-delete.sparse                 1.029       1.021   1.029   0.939   1.086  0.939–1.086
write-set-prop.dense                       0.980       1.055   0.980   0.800   0.998  0.800–0.998
write-set-prop.sparse                      0.998       0.985   0.998   0.986   1.374  0.986–1.374
```
- read geo mean: r1=0.963 r2=0.925 r3=0.982  | min=0.925 max=0.982
- write geo mean: r1=1.002 r2=0.940 r3=1.138  | min=0.940 max=1.138

配对校验（raw CSV，params_json 配对，nrecords 一致率）：
- rep1 param: 共同参数键 1400，nrecords 不一致 0
- rep1 agg: 共同参数键 2，nrecords 不一致 0
- rep2 param: 共同参数键 1400，nrecords 不一致 0
- rep2 agg: 共同参数键 2，nrecords 不一致 0
- rep3 param: 共同参数键 1400，nrecords 不一致 0
- rep3 agg: 共同参数键 2，nrecords 不一致 0

## 图算法（BFS 50 种子 depth3 cap1e6；PR 20 轮固定 d=0.85；WCC 至收敛）
ratio = PET total_s / Neo total_s（BFS 取 mean lat）；PR 三口径：total(20it)、per-iter geo、首轮；WCC 三口径：收敛总耗时、固定前 6 轮、轮数。
```
[bfs]
  rep1: mean-ratio=1.124 med=0.860 p95=1.188 | neo mean=1.098s pet mean=1.234s failed=0/0
  rep2: mean-ratio=1.165 med=0.994 p95=1.361 | neo mean=1.056s pet mean=1.230s failed=0/0
  rep3: mean-ratio=1.214 med=1.089 p95=1.328 | neo mean=1.025s pet mean=1.245s failed=0/0

[pagerank]
  rep1: total20-ratio=1.386 periter-geo=1.384 iter1=1.119 | neo=86.0s pet=119.1s top3一致(1e-6)=True
  rep2: total20-ratio=1.153 periter-geo=1.161 iter1=1.018 | neo=100.4s pet=115.8s top3一致(1e-6)=True
  rep3: total20-ratio=1.099 periter-geo=1.094 iter1=1.388 | neo=111.6s pet=122.6s top3一致(1e-6)=True

[wcc]
  rep1: conv-total-ratio=0.893 first6-ratio=0.988 | neo=61.8s/7it pet=55.1s/6it comp=3275/3275
  rep2: conv-total-ratio=0.802 first6-ratio=0.890 | neo=75.0s/7it pet=60.2s/6it comp=3275/3275
  rep3: conv-total-ratio=0.959 first6-ratio=1.067 | neo=67.2s/7it pet=64.4s/6it comp=3275/3275

```
## 自定义分析查询 q1–q5（warmup 5 + reps 100 × 冻结参数集；非 BI 声明）
```
query                       r mean-ratio r med-ratio      r1      r2      r3  empty(neo/pet, r1)
q1-country-year-window             0.866       0.839   0.866   0.708   0.817  0/0
q2-tag-monthly-topk                0.679       0.713   0.679   0.542   0.551  18/18
q3-degree-distribution             1.010       1.019   1.010   0.939   1.055  0/0
q4-influence-3hop                  0.824       0.755   0.824   0.632   1.193  0/0
q5-thread-depth                    1.017       1.023   1.017   0.871   0.963  64/64
```
- analysis geo mean (rep1) = 0.870
- analysis geo mean (rep2) = 0.724
- analysis geo mean (rep3) = 0.886

配对校验（分析 raw CSV 行序配对，nrecords 一致率）：
- rep1: q1-country-year-window: 行数 105/105，不一致 0；q2-tag-monthly-topk: 行数 105/105，不一致 0；q3-degree-distribution: 行数 105/105，不一致 0；q4-influence-3hop: 行数 105/105，不一致 0；q5-thread-depth: 行数 105/105，不一致 0
- rep2: q1-country-year-window: 行数 105/105，不一致 0；q2-tag-monthly-topk: 行数 105/105，不一致 0；q3-degree-distribution: 行数 105/105，不一致 0；q4-influence-3hop: 行数 105/105，不一致 0；q5-thread-depth: 行数 105/105，不一致 0
- rep3: q1-country-year-window: 行数 105/105，不一致 0；q2-tag-monthly-topk: 行数 105/105，不一致 0；q3-degree-distribution: 行数 105/105，不一致 0；q4-influence-3hop: 行数 105/105，不一致 0；q5-thread-depth: 行数 105/105，不一致 0
