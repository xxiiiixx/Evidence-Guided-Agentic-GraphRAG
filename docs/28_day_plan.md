# 证据可验证的自适应检索智能体 — 28 天计划

> Day 1 = **2026-10-20** · Day 21 = **2026-11-09** · Day 28 = **2026-11-16**
> 每天 6 h。**每天必须落一个可验证文件**（产物列）。验收不过，次日先补，不加新功能。

## Week 1（D1–7，10/20–10/26）：服务器环境 + 工具层 + Baseline

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **1** | 10/20 | **服务器侧**：`nvidia-smi`、`docker --version`、`python3 -V`、conda 建 3.10 env；拉仓库；`docker compose -f data/C9/docker-compose.yml up -d`；确认 `neo4j-init` 退出码 0；跑节点/关系计数 | `results/neo4j_stats.json`、`docs/env.md` | 计数与 CSV 统计一致或差异有解释 |
| **2** | 10/21 | ① `config.py` 改环境变量 + `to_dict()` 落盘 ② `main.py` 加 `--question/--batch/--mode` ③ Milvus 起（或 Milvus Lite 兜底）④ 跑通 3 个问题 | `results/smoke_trace.jsonl` | `--question` 单条可跑 |
| **3** | 10/22 | 实体链接器：Aho-Corasick 匹配 3229 个实体 + `synonyms` 扩展；20 条中文 query 人工核验 | `rag_modules/entity_linker.py` + 单测 | 准确率 ≥ 85% |
| **4** | 10/23 | 修 `_find_entity_relations`：一跳真实 Cypher，保留 nodeId / relId / 方向 | 代码 + `results/path_samples.json` | 能返回真实路径并贴出 nodeId |
| **5** | 10/24 | 修 `_find_shortest_paths`：`shortestPath` + 深度/条数上限 | 代码 + `tests/test_graph_paths.py` | 两跳可查，无路径爆炸 |
| **6** | 10/25 | ① 路径等价规则落地 ② Benchmark 生成器（双来源过滤） | `evaluation/benchmark_builder.py`、`dev.jsonl` | 40 条 dev，Gold 可回查 |
| **7** | 10/26 | **B0/B1/B2 实测** + Week 1 Go/No-Go | `results/baseline_v0.json` | ≥5 条 trace 可展示；三门槛全绿 |

**Week 1 门槛**（任一不过 → Week 2 不加新功能，先补）：

- [ ] 端到端 ≥5 问题跑通，trace 落盘
- [ ] `_find_entity_relations` 返回真实路径
- [ ] Benchmark 40 条，Gold 可回查率 ≥ 80%

## Week 2（D8–14，10/27–11/02）：判定器 + 控制器 + Agent 指标

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **8** | 10/27 | `rag_modules/evidence.py`：统一 Evidence（evidence_id, source, node_ids, edge_ids, score, verified）+ 跨源去重 | 代码 + `tests/test_evidence.py` | 同事实跨源去重有 case |
| **9** | 10/28 | 证据子图构建 + `assess()` 连通性判定 | `rag_modules/sufficiency.py` | 正例判充分、"两实体无关联"反例判不足 |
| **10** | 10/29 | **E1：判定器 AUC 实验**（项目成立与否看这个） | `results/e1_auc.json` | AUC > 0.7 才继续 |
| **11** | 10/30 | `evidence_controller.py`：状态机 + 预算 + 动作选择 | 代码 + `tests/test_controller.py` | 不死循环、不超预算、Mock 可测 |
| **12** | 10/31 | Oracle 动作序列表 + **E2：Agent 决策指标** | `results/oracle.json`、`results/e2_agent.json` | 决策准确率可算 |
| **13** | 11/01 | 接入 `--mode baseline/adaptive`，跑 40 条 dev | `results/adaptive_dev.jsonl` | 每条有 state→action 轨迹 |
| **14** | 11/02 | 错误分类 + **Week 2 Go/No-Go** | `docs/error_taxonomy.md` | ≥3 个失败案例能解释 |

**Week 2 门槛**：

- [ ] E1 有结果（**没有它，整个故事不成立**）
- [ ] E2 有 oracle 对照
- [ ] Controller 有完整 `state → action → observation → stop` 轨迹，可回放手算

> **若 Day 10 AUC < 0.6 → 停止推进。** 通常意味着：(a) 人工标注定义模糊，(b) 连通性判据太粗，(c) 图的密度过高。三条都有改法，但要先诊断。

## Week 3（D15–21，11/03–11/09）：对照 + 消融 + 鲁棒性 + 交付

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **15** | 11/03 | B3 实现并跑 | `results/b3.jsonl` | 与 B1/B2 同一 test 集，可复算 |
| **16** | 11/04 | B4 / **B5（LLM 自评）** 跑 | `results/b4_b5.jsonl` | B5 有 LLM 自评判定记录 |
| **17** | 11/05 | 冻结 test，Ours 跑 160 条 | `results/ours_test.jsonl` | test 冻结后未改动 |
| **18** | 11/06 | 消融 A1 / A2 / A6 | `results/ablation_a1a2a6.json` | A1 必须完成 |
| **19** | 11/07 | 消融 A3 / A4 / A5 | 同上 | 六组消融合计齐全 |
| **20** | 11/08 | **E4 图扰动鲁棒性** + E5 模型交叉验证 | `results/e4_perturb.json`、`e5_model.json` | 10/20/30% 删边曲线有数 |
| **21** | 11/09 | 统计（Bootstrap CI）+ 成本分析 + **Week 3 Go/No-Go** | `results/final_metrics.json`、`cost.json` | Ours vs B3 vs B5 有真实数字 + 95% CI |

**Week 3 门槛**：

- [ ] Ours vs B3 vs B5 有**真实数字** + 置信区间
- [ ] 六组消融齐全（少一两组可接受，但 A1 必须有）
- [ ] ≥5 成功 + 5 失败案例，每个能讲清归因
- [ ] **负结果不隐藏**——若 Ours 没赢，写"没赢，原因是 X"

## 缓冲期（D22–28，11/10–11/16）

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **22** | 11/10 | 补 Week 3 缺口（最可能超时的是消融和扰动实验） | 按缺口定 | Week 3 三门槛全绿 |
| **23** | 11/11 | `scripts/run_baseline.py` / `run_experiment.py` / `smoke_test.py` 一键复现 | 三个脚本 | 干净环境能跑通 |
| **24** | 11/12 | README 重写（问题→方法→实验→限制）+ 架构图 | `README.md` | 30 秒能看懂价值 |
| **25** | 11/13 | 简历 3 条 + 2 / 5 分钟讲述稿 | `docs/pitch.md` | 只用实测数字 |
| **26** | 11/14 | 模拟面试 20 题 | `docs/interview.md` | 每题能扛 2 层追问 |
| **27** | 11/15 | limitations 文档（把失败变成 future work） | `docs/limitations.md` | 每个失败案例有归因 |
| **28** | 11/16 | 全新环境复现 + Release tag | tag | 复现成功 |
