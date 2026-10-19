# 28 天计划 v2（重排版）

> 替代 `28_day_plan.md`。原计划保留作历史记录，不再作为执行依据。
> 起始：2026-10-20（Day 1 已于 10-15 完成，见 §0）
> 目标：DeepSeek / 腾讯 / 字节 算法岗（Agent / RAG 方向）面试可深挖 40 分钟
> 时间预算：112 小时，按周 Go/No-Go 动态调整
> 状态标记约定：`[未开始]` `[进行中]` `[已完成-有产物]` `[已完成-无产物=视为未完成]`

---

## 0. Day 1 补课（明天上午优先做）

原 Day 1 只交了源码笔记，缺运行证据。以下必须在 Day 2 之前完成，否则后面全部悬空。

| # | 任务 | 产物 | 状态 |
| --- | --- | --- | --- |
| 0.1 | `docker compose -f data/C9/docker-compose.yml up -d`，等 healthcheck 通过 | `docker ps` 输出 | `[未开始]` |
| 0.2 | 确认 `neo4j-init` 执行完毕，查节点/边计数 | `results/neo4j_stats.json` | `[未开始]` |
| 0.3 | 记录环境版本 | `docs/day00_env.md`（python / docker / compose / CUDA / GPU / git SHA） | `[未开始]` |

`neo4j_stats.json` 至少包含：

```json
{
  "labels": {"Recipe": 323, "Ingredient": 2906, "CookingStep": 2514, "RecipeCategory": 9},
  "relationships": {"REQUIRES": 2905, "CONTAINS_STEP": 2514, "BELONGS_TO": 322, "DIFFICULTY_LEVEL": 314},
  "source_commit": "85919c1",
  "captured_at": "2026-10-20T10:30:00+08:00"
}
```

> 上面数字是我从 CSV 统计得到的**预期值**（`nodes.csv` 5761 行、`relationships.csv` 6056 行）。你跑出来的数字如果对不上，**以数据库为准**，并在文档里记下差异原因（比如 CSV 第一行是表头、或导入时被 MERGE 去重）。

---

## 1. 项目定位（这是 v2 最重要的改动）

### 1.1 旧定位的问题

原计划：**"把 C9 GraphRAG 改造成证据反馈 + 预算的自适应检索系统"**

这是**工程改进**句式。面试官听完的反应是："所以你就是加了个 if 判断？"

### 1.2 新定位

> **标题**：证据充分性的连通性判定 —— 面向 GraphRAG 的可验证检索停止准则
>
> **一句话**：现有 RAG 用文本相似度判断"检索够了没"，但相似度无法证明多条证据**共同**支持一个多跳结论。本文把证据充分性定义为查询实体子图上的**连通性覆盖**问题，给出 O(|E|·k) 的可计算判定，并在工具预算约束下求解检索策略。
>
> **可证伪的核心假设**：在需要多实体关联的问题上，**连通性覆盖判定**比 **top-k 相似度阈值** 更能预测答案正确性（AUC 更高），且据此停止能让工具调用下降而准确率不降。

### 1.3 为什么这个定位能过面试

| 面试官会问 | 旧定位的答案 | 新定位的答案 |
| --- | --- | --- |
| 你的 insight 是什么 | "我做了反馈控制" | "充分性不是相似度问题，是连通性问题" |
| 和 CRAG/Self-RAG 区别 | "我用实体覆盖"（特征替换） | "它们在文本层面打分，我在图结构层面做**判定**，后者可证明" |
| 怎么验证 | "看准确率涨没涨" | "先验证判定器本身的 AUC，再做端到端" |
| 失败了怎么办 | 没想过 | "假设被证伪也是一篇负结果，可解释为什么" |

### 1.4 明确不做

- 不做 Multi-Agent、不做 UI、不做 SFT/LoRA、不做通用知识图谱构建
- 不用 LangGraph（你是要展示**算法**，不是要展示**框架熟练度**）
- 不追求 200 条以外的规模扩张

---

## 2. 方法设计

### 2.1 形式化

**定义（证据充分性）**。给定查询 `q`，抽取必需实体集 `E_q = {e_1..e_n}` 与必需关联集 `R_q ⊆ E_q × E_q`。给定已检索证据集 `S`（每个 evidence 携带 node_ids / edge_ids）。称 `S` 对 `q` **充分**，当且仅当：

1. **实体覆盖**：`E_q ⊆ ⋃_{s∈S} nodes(s)`
2. **连通覆盖**：`∀(e_i,e_j) ∈ R_q`，`S` 诱导的子图中 `e_i` 与 `e_j` 连通
3. **预算合规**：`cost(S) ≤ B`

三条件缺一不可。条件 2 是本文主张的核心——它把"两个实体都出现了"升级为"两个实体在证据里被**同一条链**连起来了"。

**与现有做法的差别**：

| 方法 | 充分性判据 | 能否证明多跳结论 |
| --- | --- | --- |
| 朴素 RAG | `top_k` 非空 | 否 |
| Self-RAG | LLM 生成 relevance token | 否（概率性） |
| CRAG | 分类器打 correct/incorrect/ambiguous | 否（文本层面） |
| **本文** | 子图连通性 + 覆盖 | **是（可判定）** |

### 2.2 判定算法

```
输入: E_q, R_q, S, B
1. 构建证据子图 G_S = (⋃nodes(S), ⋃edges(S))
2. 实体覆盖检查: missing_e = E_q \ nodes(G_S)
   若 missing_e ≠ ∅ → 返回 (INSUFFICIENT, missing_entities=missing_e)
3. 连通性检查: 对每个 (e_i,e_j) ∈ R_q
     若 not connected(G_S, e_i, e_j) → 记录 missing_relation
4. 若 relatedness_required 且 missing_relation ≠ ∅ → 返回 (INSUFFICIENT, ...)
5. 若 cost(S) > B → 返回 (OVER_BUDGET, ...)
6. 返回 (SUFFICIENT, support_paths=...)
```

复杂度：`O(|V|+|E|)` 一次 BFS 可解全部连通性。**这是廉价的**，不需要 LLM 调用——这点很重要，因为它意味着判定器本身不消耗预算。

### 2.3 控制器

```
state = (q, E_q, R_q, evidence, tool_history, remaining_budget)
loop:
  verdict, missing = assess(state)
  if verdict == SUFFICIENT:        → stop
  if remaining_budget == 0:        → abstain 或 cautious_answer
  action = choose_action(missing, tool_history)   # 规则/贪心
  new_ev = run_tool(action, q)                     # hybrid | graph
  state.evidence = fuse(state.evidence, new_ev)    # 含去重
```

**`choose_action` 的策略**（这是可讲的部分）：

- `missing_entities ≠ ∅` → 选 **Hybrid**（实体召回靠文本检索更稳）
- 仅有 `missing_relations` → 选 **Graph**（连通性只能靠图补）
- 两者都有且预算 ≥ 2 → 先 Hybrid 再 Graph（先补实体，再补关系）
- 该工具已调用过且无新增 → 换另一个

**贪心的失效条件**（面试必问，提前想好）：两个工具单独都补不齐、但组合能补齐时，贪心会因单步无增益而提前停止。你的 Benchmark 应该**刻意包含这类问题**，用来展示失败。

### 2.4 预算与成本模型

不要只说"预算 2 次"。定义清楚：

| 层级 | 计数单位 | 典型值 |
| --- | --- | --- |
| 顶层工具调用 | 一次 `hybrid_search` 或 `graph_rag_search` | 1 或 2 |
| 底层检索 | Hybrid 内部的 dense + bm25 + dual-level | 3 |
| LLM 调用 | 每次 `understand_graph_query` / `analyze_query` / 生成 | 1–3 |
| Token | 输入 + 输出 | 记录 |

**关键**：`graph_rag_search` 内部会调 LLM 做意图理解（`graph_rag_retrieval.py:493`），所以一次 Graph 检索的 LLM 成本远高于 Hybrid。**这个不对称必须写进成本模型**，否则"省工具调用"的说法站不住。

---

## 3. 每周安排

### Week 1：环境 + Baseline + 入口改造（P0，约 27h）

| Day | 任务 | 产物 | 验收 |
| --- | --- | --- | --- |
| **1（补）** | Neo4j 起 + 导入 + 计数；环境版本 | `results/neo4j_stats.json`、`docs/day00_env.md` | 计数与 CSV 一致或差异有解释 |
| **2** | ① `config.py` 改环境变量加载 ② `main.py` 加 `--question/--batch` 非交互入口 ③ Milvus 起 | `results/smoke_trace.jsonl` | 能 `python main.py --question "..."` 单条跑通 |
| **3** | 实体抽取：用 3229 个已知实体名做词典匹配（Aho-Corasick）+ `synonyms` 扩展 | `rag_modules/entity_linker.py` + 单测 | 对 20 条中文 query 提取实体，人工核验准确率 ≥ 80% |
| **4** | 修 `_find_entity_relations`：一跳真实 Cypher，保留 nodeId / relId / 方向 | 代码 + `results/path_samples.json` | 对"哪道菜含鸡肉"能返回真实路径 |
| **5** | 修 `_find_shortest_paths`：`shortestPath` + 深度/条数上限 | 代码 + 单测 | 两跳路径可查，无路径爆炸 |
| **6** | ① 定义"路径等价"规则 ② Benchmark 生成器（文本金标准，见 §4） | `evaluation/benchmark_builder.py`、`data/benchmark/dev.jsonl` | 40 条 dev，路径可回查 |
| **7** | **B0/B1/B2 实测** + Week 1 Go/No-Go | `results/baseline_v0.json`、`docs/week01_report.md` | 至少一条完整 trace 可展示 |

**Week 1 门槛（Go/No-Go）**：

- [ ] 端到端跑通至少 5 个问题，有 trace 落盘
- [ ] `_find_entity_relations` 返回真实路径，能贴出 nodeId
- [ ] Benchmark 40 条，每条 Gold 可回查
- [ ] **若未达成** → 不进入 Week 2，砍掉 B4/部分消融，先保 MVP

### Week 2：核心算法（P0，约 33h）

| Day | 任务 | 产物 | 验收 |
| --- | --- | --- | --- |
| **8** | `rag_modules/evidence.py`：统一 Evidence 结构（evidence_id, source, node_ids, edge_ids, score, verified） | 代码 + `tests/test_evidence.py` | 跨源同事实去重有 case |
| **9** | 证据子图构建 + 连通性判定 `assess()` | `rag_modules/sufficiency.py` | 对正例判充分、对"两实体无关联"反例判不足 |
| **10** | **判定器单独评估**：构造 60 条"充分/不足"标注，算 AUC | `results/sufficiency_auc.json` | AUC > 0.7 才继续 |
| **11** | `evidence_controller.py`：状态机 + 预算 + 动作选择 | 代码 + `tests/test_controller.py` | 不死循环、不超预算、Mock 可测 |
| **12** | 接入 `main.py --mode adaptive`，保留 baseline | 代码 | `--mode baseline/adaptive` 都能跑 |
| **13** | 跑 40 条 dev，记录 trace | `results/adaptive_dev.jsonl` | 每条有 state→action 轨迹 |
| **14** | 错误分类：错误路由 / 早停 / 无效补检索 / 生成错误 | `docs/error_taxonomy.md` | 每类至少 3 例 |
| **15** | **Week 2 Go/No-Go** | `docs/week02_report.md` | 见下 |

**Week 2 门槛**：

- [ ] §10 的判定器 AUC 实验有结果（**这是项目的核心证据，没有它整个故事不成立**）
- [ ] Controller 有完整 trace，可回放手算
- [ ] 至少 3 个失败案例能解释原因

> **如果 Day 10 的 AUC < 0.6**：说明连通性判定不如相似度，**立即调整**——回到导师讨论，不要硬推到 Day 28。这本身是可以写进文档的负结果。

### Week 3：对照实验 + 消融（P0，约 30h）

| Day | 任务 | 产物 |
| --- | --- | --- |
| **16** | B3（共享修复的 One-shot）实现并跑 | `results/b3.jsonl` |
| **17** | B4（Always-Combined）跑 | `results/b4.jsonl` |
| **18** | 冻结 test 集，Ours 跑测试集 | `results/ours_test.jsonl` |
| **19** | 消融 A1（去连通性，仅实体覆盖）/ A2（去反馈）/ A3（不补 Graph）/ A4（预算 1 vs 2） | `results/ablation.json` |
| **20** | 统计：Bootstrap 95% CI，按问题类型分桶 | `results/final_metrics.json` |
| **21** | 成本分析：工具调用 / token / P50-P95 延迟 | `results/cost.json` |
| **22** | **Week 3 Go/No-Go** + 失败案例写作 | `docs/failure_analysis.md` |

**Week 3 门槛**：

- [ ] Ours vs B3 有**真实**数字，含置信区间
- [ ] 四组消融齐全
- [ ] 至少 5 成功 + 5 失败案例，每个能讲清归因
- [ ] **负结果不隐藏**（如果 Ours 没赢，文档必须写"没赢，原因是 X"）

### Week 4：交付物 + 面试打磨（P0+P1，约 22h）

| Day | 任务 | 产物 |
| --- | --- | --- |
| **23** | `scripts/run_baseline.py` / `run_experiment.py` / `smoke_test.py` 一键复现 | 脚本 |
| **24** | README 重写（问题→方法→实验→限制） | `README.md` |
| **25** | 架构图 + 判定算法伪代码图 | `docs/architecture.md` + 图 |
| **26** | 简历 3 条（只用实测数字）+ 2 分钟/5 分钟讲述稿 | `docs/pitch.md` |
| **27** | 模拟面试 20 问（见 `interviewer_guide.md`） | `docs/mock_interview.md` |
| **28** | 全新环境复现验证 + Release tag | 通过记录 |

---

## 4. Benchmark 设计（修正原计划的核心缺陷）

### 4.1 反 Graph 偏差：文本金标准

原计划的所有问题都从 Cypher 生成，必然偏向图。**修正方案**：

**每条测试用例同时具备两个来源**：

1. **图来源**：`MATCH (r:Recipe)-[:REQUIRES]->(i:Ingredient)` 得到结构化答案与路径
2. **文本来源**：从 `r.description` 或 `CookingStep` 文本中确认答案

**判定规则**：只保留**两个来源都支持**的样本。这样 Graph 和 Hybrid 都有机会答对，偏差被消除。

对"图中无解"的负例，改成"**文本中也找不到**"，避免把"图谱不完整"误当成"信息不存在"。

### 4.2 路径等价定义

不要存"一条路径"，存**必须覆盖的证据三元组集合**：

```json
{
  "query_id": "q001",
  "question": "哪道菜同时用到鸡肉和土豆？",
  "required_entities": ["鸡肉", "土豆"],
  "required_relations": [["鸡肉", "土豆"]],
  "minimal_evidence": [
    {"recipe_node_id": "201000011"},
    {"ingredient_node_ids": ["201000012", "201000045"]}
  ],
  "answer": "咖喱鸡",
  "source": "both",
  "bucket": "two_hop"
}
```

判定：Agent 的证据中若能找到**同一个** recipe 同时连到两个 ingredient，即算命中。**不要求路径的跳数或中间节点**。这样避免"合法但不同"被误判。

### 4.3 数据分布（200 条）

| 类型 | 目标条数 | 关键设计 |
| --- | ---: | --- |
| 单实体/单关系 | 50 | 一跳，Hybrid 和 Graph 都应能答 |
| 两跳关联 | 60 | Ingredient→Recipe→Ingredient |
| 组合条件 | 40 | 多食材交集，**易假阳性** |
| 无答案 | 30 | 图与文本均无 |
| 贪心失效 | 20 | 单工具无法补齐，需组合 |

Dev 40 / Test 160，按 entity 分组划分防泄漏。

---

## 5. Baselines

| ID | 方法 | 用途 |
| --- | --- | --- |
| B0 | Original As-Is | 缺陷记录，**不用于算法对比**（见评审 §3.2：ENTITY_RELATION 恒空） |
| B1 | 仅 Hybrid | 文本基线 |
| B2 | 仅 Graph（修复后） | 图基线 |
| B3 | **共享修复的 One-shot Router** | **核心对照** |
| B4 | Always-Combined | 质量–成本上界 |
| **Ours** | 连通性判定 + 预算控制 | 待验证 |

**语义消融**（新增，比参数消融更重要）：

| ID | 改动 | 验证什么 |
| --- | --- | --- |
| A1 | 充分性判据退化为"实体覆盖 only"（去掉连通性） | **连通性是否真的必要** ← 核心 |
| A2 | 去掉反馈，一次检索后直接生成 | 反馈是否必要 |
| A3 | 补检索只用 Hybrid，不用 Graph | Graph 补检索的价值 |
| A4 | 预算 1 vs 2 vs 3 | 预算敏感性 |
| A5 | 判定器换成"top-1 相似度 > 阈值" | 与相似度基线对比 |

---

## 6. 指标

**判定器层**（Week 2 就有）：
- 充分性判定 AUC、Precision/Recall（相对人工标注）
- **这是最有说服力的指标**，因为它不依赖生成质量

**检索层**：
- Evidence Recall@5（按 §4.2 定义）
- Path Validity（返回路径中真实存在于图的比例）

**端到端**：
- Answer Set-F1 / EM
- 无答案拒答正确率
- Task Success Rate

**成本**：
- 平均顶层工具调用数、底层检索数、LLM 调用数
- Token、P50/P95 延迟

**统计**：Bootstrap 95% CI，按 bucket 分桶，成对比较（同一 query 上对比方法）。

---

## 7. 风险与降级

| 风险 | 触发信号 | 降级动作 |
| --- | --- | --- |
| 环境起不来 | Day 1 结束 Neo4j 未通 | 先用 Neo4j Desktop 或远程实例，Milvus 后置 |
| 判定器 AUC 低 | Day 10 AUC < 0.6 | 降级为"实体覆盖 + 关系类型匹配"启发式；把这个负结果写进报告 |
| Benchmark 质量差 | Day 6 可回查率 < 80% | 缩到 80 条，优先质量 |
| Ours 不如 B3 | Week 3 | 写"为什么没赢"的分析，这仍是合格的交付 |
| 时间不够 | 任一周末 Go/No-Go 失败 | 砍 A4/A5、砍 B4，保 MVP |

---

## 8. 三档交付

| 等级 | 内容 |
| --- | --- |
| **MVP（保底）** | 可复现 Baseline + 图路径修复 + 80 条 Benchmark + 至少两组对照 + 失败分析 |
| **标准（目标）** | 上述 + 连通性判定器 AUC 实验 + 160 条 test + 4 组消融 + 成本分析 |
| **进阶** | 上述 + 贪心失效分析 + 与相似度阈值的系统对比 + 开源可复现脚本 |

---

## 9. 每日工作流（固定动作）

1. **开始**：写今天要产出的**文件名**（不是"学习 X"）
2. **结束**：`git commit`，commit message 写"做了什么 + 产物路径"
3. **睡前**：在 `docs/daily_log.md` 加一行：`Day N | 产出 | 卡点 | 明天第一件事`

不允许出现"今天读了代码"这种 commit。读代码的产物是**文档里新增的一段有引用的分析**。

---

## 10. Day 10 关键实验（单独强调）

**这是整个项目成立的证据，必须认真做。**

**问题**：连通性覆盖判定，是否比 top-k 相似度更能预测答案正确性？

**做法**：

1. 取 60 条 dev query（覆盖四个 bucket）
2. 对每条，用**同一个**固定检索器（B3）拿到证据集
3. 分别计算：
   - `score_conn`：连通性判定结果（二值）
   - `score_sim`：top-1 证据的相似度分数
4. 人工标注每条 query 的"证据是否真的充分"（不看答案，只看证据）
5. 算两个 score 对人工标注的 AUC

**预期结果**：`score_conn` AUC 更高（因为它是确定性的，而相似度对多跳问题是 noise）。

**如果结果相反**，这是重要的负结果——说明在这个数据集上相似度够用，你需要重新思考。**不要隐藏。**

---

## 11. 每周日 Go/No-Go 检查表

```
[ ] 本周是否产出了可验证文件？（不是文字）
[ ] 现在能否对着代码讲 10 分钟本周工作？
[ ] 有无未提交代码？
[ ] 下周如果时间减半，砍哪一项？
[ ] 项目整体：如果今天面试，能讲 20 分钟吗？
```

任何一项打叉，**下周先补，不加新功能**。
