# Agent/RAG 算法实习导向：28 天项目开发与学习计划

> 起始版本：`cbe0b8a2c9e5290ee83ef7dd0a58cb7d00148c9f`（独立项目 `main`，2026-10-14）  
> 上游：Datawhale `all-in-rag` 的 `code/C9` 和 `data/C9`，来源 Commit `cec956e80ae28d6c0ca84c9d39dddcf61ad633fd`  
> 项目周期：**不超过 28 天**；时间预算约 **112 小时**，按实际进度调整  
> 计划状态：**尚未执行完毕**；下面的实验目标、交付物、阈值均非已实现结果

## 1. 核心目标和不可逾越的范围

将现有菜谱知识 GraphRAG Baseline 改造成**基于证据反馈与工具预算的自适应检索系统**，形成可复现的 Algorithm + Benchmark + Ablation + Failure Analysis，满足 Agent/RAG 算法实习面试的项目深挖要求。

**唯一核心算法方向：Evidence-Guided Budget-Aware Adaptive Retrieval。**

- **核心改动**：基于已召回证据的实体/关系覆盖、可验证路径和预算，决定继续检索哪个工具、停止或谨慎拒答。
- **必要辅助 1**：修复或限制与实验有关的 Graph 检索空实现，保存真实节点/边 ID 和关系方向。
- **必要辅助 2**：构建固定数据集、指标、执行 trace 和实验脚本。
- **不做**：新建通用 Agent 框架、Multi-Agent、复杂 UI、训练大模型、重新构建通用知识图谱、为了技术名词引入 LangGraph。
- 结果优先级：**可运行、可解释、可验证、能复现** > 堆砌功能。

### 已确认的代码背景

- `main.py::ask_question_with_routing`：路由返回文档非空后直接进入生成；没有显式的证据充分性反馈。
- `rag_modules/intelligent_query_router.py::route_query`：一次性 Hybrid/Graph/Combined 路由；检索异常时回退不等于低质量证据的补检索。
- `rag_modules/hybrid_retrieval.py::hybrid_search`：已有 Dual + Dense + BM25，使用 RRF；已有可选父文档回填，均应保留。
- `rag_modules/graph_rag_retrieval.py::_find_entity_relations/_find_shortest_paths`：返回空列表；推理链验证存在占位逻辑。
- 详见 [Day 1 Baseline 分析](baseline_analysis.md)。**源码已检查，运行尚未验证**。

## 2. 方法设计：原始流程、改进流程与接口

```text
Baseline: Query → LLM 一次性路由 → Hybrid / Graph / Combined → Documents → LLM

Ours: Query → 初次路由与检索 → 证据标准化/核验 → Evidence Assessment
                                             │
                           ┌─充分 ────────────┴─→ Stop → 证据约束生成
                           ├─不充分且预算允许 ─→ 换/补检索 → 再次 Assessment
                           └─预算耗尽 ─────────→ 谨慎回答或 Abstain
```

### 2.1 状态、动作、特征

`RetrievalState(query, required_entities, evidence, tool_history, remaining_budget, decision_trace)`

`Evidence(evidence_id, source, content, node_ids, edge_ids, score, verified, metadata)`

动作：`hybrid`、`graph`、`stop`、`abstain`。初始工具调用预算建议设为 **2 次顶层检索**，再通过消融比较预算 1/2；由于一次 Hybrid 内部包含多路检索，另外记录底层 API / 工具调用次数。

暂定证据充分性特征：

- **Entity Coverage**：查询所需实体被结构化证据覆盖的比例。
- **Relation Coverage**：所需关系有经 Neo4j 验证的证据路径支持的比例。
- **Retrieval Relevance**：检索排名/匹配特征，不把 RRF、BM25、COSINE 分数直接当成可比概率。
- **Evidence Novelty**：新增信息比例，避免相同证据重复召回。

一个候选评分器：`C = w_e * C_entity + w_r * C_relation + w_q * C_relevance`，其中 `w_e+w_r+w_q=1`，权重仅在开发集调试。多跳任务应额外要求关系路径有效，不能仅凭关键词覆盖停止。

### 2.2 计划接口与文件

| 文件 | 动作 | 描述 |
| --- | --- | --- |
| `rag_modules/evidence.py` | 新增 | Evidence 标准化、去重、路径身份与验证结果 |
| `rag_modules/evidence_controller.py` | 新增 | assess / choose_action / run，预算与停止逻辑 |
| `rag_modules/graph_rag_retrieval.py` | 最小修改 | 一/两跳路径、必要查询修复、节点边 ID/关系方向 |
| `rag_modules/intelligent_query_router.py` | 尽量保留 | 作为固定 One-shot Baseline，不覆盖其原始策略 |
| `rag_modules/hybrid_retrieval.py` | 尽量保留 | 仅补必要 trace / 标准证据 ID；不重写 RRF |
| `rag_modules/generation_integration.py` | 少量修改 | 带证据编号的上下文、拒答和可追溯引用 |
| `main.py` / `config.py` | 轻改 | `baseline` / `adaptive` 模式，预算、阈值与输出路径 |
| `evaluation/`、`tests/`、`scripts/` | 新增 | Benchmark、客观指标、单测及实验脚本 |

建议目录（仅在需要时创建，不要第一天生成大量空模块）：

```text
rag_modules/
  evidence.py
  evidence_controller.py
  # 原有 graph_rag_retrieval.py, hybrid_retrieval.py 等保留
evaluation/
  benchmark_builder.py
  evaluate_retrieval.py
  evaluate_answer.py
  evaluate_agent.py
  report.py
tests/
  test_graph_paths.py
  test_evidence.py
  test_controller.py
scripts/
  run_baseline.py
  run_experiment.py
  smoke_test.py
data/benchmark/
  dev.jsonl
  test.jsonl
results/
docs/
  baseline_analysis.md
  28_day_plan.md
  architecture.md
  experiments.md
  failure_analysis.md
```

## 3. Benchmark、对照实验与评价指标

### 3.1 数据集

优先使用 `data/C9` 的**真实图节点/关系**生成可执行 Gold，避免第一轮迁移外部百科语料与重建全部图。目标规模 **200 条**：开发集 40 / 测试集 160。

| 类型 | 目标条数 | Gold 设计 |
| --- | ---: | --- |
| 单实体 / 单关系 | 60 | Recipe → Ingredient 等单跳结构 |
| 两跳关联 | 70 | Ingredient → Recipe → Ingredient / Category |
| 组合条件 | 30 | 多食材、分类等条件交集 |
| 当前图中无解 / 信息不足 | 40 | 基于数据库查询确认无解；不等于现实事实不存在 |

若标注质量与时间不足，则降级为 **80–100 条高质量样本**，优先保证正确性、人工核验和独立测试集。

构造顺序：Cypher 取得答案及支持路径 → 模板提问 → 去重与人工改写 → 核验正负例 → 按实体组合/模板划分 Dev/Test → 冻结 Test。**不得**将 Gold 答案/路径作为 Agent 运行时输入。合成模板问题可能偏向 Graph，需要按问题类型分桶并加入自然改写。

### 3.2 Baselines

| ID | 方法 | 用途 |
| --- | --- | --- |
| B0 | Original As-Is | 原样运行与缺陷记录（不能隐藏失败） |
| B1 | 仅 Hybrid | 文本/混合检索基线 |
| B2 | 仅 Graph | 图证据检索基线 |
| B3 | Shared Repaired One-shot Router | **核心对照**：共用必要正确性修复，只保留一次性路由策略 |
| B4 | Always Combined | 每次两路检索的质量–成本参考 |
| Ours | Evidence Feedback + Budget | 核心待验证算法 |

关键：**修复 Bug 的收益与新策略的收益分开归因**。固定模型、索引、Query、top_k、候选、温度、运行条件；报告参数、日期、模型名称和版本。

### 3.3 指标与分析

**核心客观指标**：Evidence Recall@5、Path Recall@5、Path Validity、Answer Set-F1/EM、无答案拒答正确率、Task Success Rate。

**Agent / 成本指标**：首次不足时补检索恢复率、不必要补检索率、平均顶层/底层调用数、Token 成本、P50/P95 延迟。

**消融（必做 4 组）**：
1. 去掉 Relation Coverage；
2. 禁用检索后反馈；
3. 禁用 Graph 补检索；
4. 比较预算 1 和预算 2。

**统计**：固定测试集、成对比较、Bootstrap 95% 置信区间；结果按简单、多跳、无答案类型分桶；至少 5 个成功例和 5 个失败例。避免只用 LLM-as-a-Judge 给结论。

**结果占位（绝不能伪造）**：

| 方法 | Evidence Recall@5 ↑ | Answer F1 ↑ | Avg Tool Calls ↓ | P95 Latency ↓ |
| --- | --- | --- | --- | --- |
| B3 | 待实验 | 待实验 | 待实验 | 待实验 |
| B4 | 待实验 | 待实验 | 待实验 | 待实验 |
| Ours | 待实验 | 待实验 | 待实验 | 待实验 |

## 4. 28 天逐日任务表

优先级：**P0 必做，P1 推荐，P2 可选**。单日 2–5 小时；每一天同时写明开发动作、需要补充的知识、参考源码/资料、验收结果和输出文件。

### Week 1：理解项目与复现 Baseline（约 27h）

| Day | 优先级/时间 | 当日目标与具体开发任务 | 理论补充 / 学习资料 | 验收标准与输出成果 |
| --- | --- | --- | --- | --- |
| **1** | P0 / 3h | 固定上游/工作仓库 SHA；检查 Python/Git/Docker/GPU；读 `main.py` 和 `intelligent_query_router.py`；绘制调用链；记录已知缺陷与待确认风险 | Python 对象生命周期、路由策略、Hybrid 与 Combined 区别；读仓库原源码 | `docs/baseline_analysis.md`；版本和环境日志（尚需实际上传核验） |
| **2** | P0 / 4h | 安装锁定依赖；启动 `data/C9/docker-compose.yml`；检查容器健康、导入 CSV 后的节点/边数量和关系类型；存运行命令 | Neo4j `MATCH`、`WHERE`、变长路径；读 `neo4j_import.cypher` 和 Cypher 手册 | Neo4j 查询与导入统计成功；`results/neo4j_stats.json` |
| **3** | P0 / 5h | 启动独立 Milvus；运行 BGE Embedding 与索引构建；试运行 `python main.py` 的 5–10 个问题；复现 Graph 空实现/子图结构风险 | COSINE、BGE、HNSW；读 `milvus_index_construction.py` 与 Milvus 索引文档 | 一次完整检索/问答记录或明确 blocker；`results/smoke_trace.jsonl` |
| **4** | P0 / 4h | 在 `route_query()`、`hybrid_search()`、`graph_rag_search()` 加最小非侵入日志（先不改决策）；追踪 3 个实际 Query 的召回链路 | BM25 / RRF；读 `_rrf_merge()` 和 RRF 论文 | 三路召回 ID、rank、耗时可查看；数据流图初稿 |
| **5** | P0 / 4h | 新增 `evaluation/benchmark_builder.py`；用真实 Recipe/Ingredient/Category 两跳关系构建至少 20 条有答案/路径 Gold | Gold 设计、Graph 查询；读 Cypher 官方文档 | 小规模 JSONL Benchmark，可重新生成；路径均能回查 |
| **6** | P0 / 4h | 开发集目标 40 条、测试集初稿；写 `evaluate_retrieval.py`、`evaluate_answer.py` 的 Recall/F1 单测；避免同模板泄漏 | EM/F1、Recall@K、数据泄漏；参考 BEIR 评测设计 | `data/benchmark/dev.jsonl` 和初版 `test.jsonl`；指标函数单测 |
| **7** | P0 / 3h | 跑 **Original As-Is** 和可用的固定检索 Baseline；记录失败分支、模型/参数/延迟；列出必须修复的问题和可后置事项 | 失败归因、CRAG 问题动机 | `results/baseline_v0.json`、Week 1 复现报告；冻结最小修复列表 |

**Week 1 门槛**：Baseline 至少有真实检索链路与可执行 Gold；如果模型 API 失败，应先用固定/本地兼容模型完成同配置的对照，不盲目进入复杂控制器开发。

### Week 2：核心算法实现（约 33h）

| Day | 优先级/时间 | 当日目标与具体开发任务 | 理论补充 / 学习资料 | 验收标准与输出成果 |
| --- | --- | --- | --- | --- |
| **8** | P0 / 5h | 处理 `_find_entity_relations()`：实现 Recipe–Ingredient / Recipe–Category 等一跳真实检索，保留 ID 与方向 | Neo4j relationship type、方向、索引 | `tests/test_graph_paths.py` 一跳通过，错误路径不通过 |
| **9** | P0 / 5h | 在已有 `multi_hop_traversal()` 范围内实现或限制两跳检索；对 `_find_shortest_paths()` 做最低必要实现/清晰失败；限制遍历深度和结果量 | 多跳路径、图遍历复杂度、路径爆炸 | 两跳测试通过，最大深度/候选受限 |
| **10** | P0 / 5h | 修正必要的子图提取/转换问题；实现路径来源回查和有效性验证，不能使用假推理链当证据 | Provenance、关系校验、结构化证据 | 真实路径 ID / 边方向可复核；`test_graph_paths.py` |
| **11** | P0 / 5h | 新增 `rag_modules/evidence.py`，把 Hybrid 文档/Graph 路径转统一 Evidence；处理重复 node_id、chunk_id 和来源 | Evidence Fusion、RRF 归一身份问题 | `tests/test_evidence.py`；同事实跨源去重有用例 |
| **12** | P0 / 4h | 写实体覆盖、关系覆盖、路径校验和简单排序特征；对空结果、缺一关系、冲突证据做单测 | CRAG 证据质量评估；阈值与校准 | `assess(state)` 可解释，评分不依赖测试集 Gold |
| **13** | P0 / 5h | 新增 `evidence_controller.py` 的状态、工具选择、最多两次顶层检索预算、Stop/Abstain、trace；用 Mock 工具测试循环 | ReAct、有限动作策略、状态转移 | `tests/test_controller.py`：不死循环、不超预算、正常停止 |
| **14** | P0 / 4h | 将控制器作为可选 `adaptive` 模式接入 `main.py`，保留 `baseline`；跑 40 条 Dev | Adaptive-RAG 复杂度路由与反馈路由差异 | 完整 trace 和一轮 Dev 结果；原模式仍可运行 |

**Week 2 门槛**：控制器不是凭空调用 LLM“想一想”，而是依据真实证据、状态、预算产生可解释的动作。

### Week 3：实验和优化（约 30h）

| Day | 优先级/时间 | 当日目标与具体开发任务 | 理论补充 / 学习资料 | 验收标准与输出成果 |
| --- | --- | --- | --- | --- |
| **15** | P0 / 5h | 同配置运行 B1/B2/B3；记录不同 Query 类型的召回、零结果和成本 | 检索分桶、Recall vs. Precision | `results/baseline_comparison.csv` |
| **16** | P0 / 5h | 跑控制器 Dev；分类错误路由、提前停止、错误补检索、未支持答案 | Precision/Recall、反馈控制失效 | `results/adaptive_dev.jsonl`；错误分布 |
| **17** | P0 / 4h | 在 Dev 调整至多少量阈值/权重；固定评分器、工具顺序与生成配置；封存测试集 | 开发集调参、过拟合风险 | 冻结 `experiment_config`，说明选参理由 |
| **18** | P0 / 4h | 完成 A1（去 Relation Coverage）和 A2（禁用 Feedback） | 消融、控制变量 | 第一份消融对比 |
| **19** | P0 / 4h | 完成 A3（不补 Graph）、A4（预算 1 vs 2），观察成本与质量 | 成本敏感决策、Pareto | 消融完整表及成本曲线 |
| **20** | P0 / 4h | 只用冻结配置评测独立 Test；计算各类任务准确率、延迟与 Bootstrap 置信区间 | 成对统计、置信区间 | `results/final_metrics.json`（真实值才填写） |
| **21** | P0 / 4h | 精查至少 5 成功、5 失败 Trace；区分 Query 解析、检索、证据评估、决策、生成错误 | Error Attribution | `docs/failure_analysis.md`；不掩盖负结果 |

**Week 3 门槛**：不能只展示几个 Demo；必须能回答“为什么比 One-shot 好/差、改善来自哪一步、为此付出什么成本”。

### Week 4：可复现交付与求职材料（约 22h）

| Day | 优先级/时间 | 当日目标与具体开发任务 | 理论补充 / 学习资料 | 验收标准与输出成果 |
| --- | --- | --- | --- | --- |
| **22** | P0 / 4h | 整理变量/接口、统一配置和错误处理、锁定 Python/依赖版本；移除个人调试代码 | 可复现工程、依赖管理 | Smoke Test 从新环境可运行 |
| **23** | P0 / 4h | 完成 `scripts/smoke_test.py`、`run_baseline.py`、`run_experiment.py`；提供文本 CLI Demo，无需前端 | 输入输出契约、日志与测试 | 一键生成可核验指标文件 |
| **24** | P0 / 3h | 绘制 Baseline/Ours 架构图与状态图，整理参数、消融表、图路径样例和实验脚本 | Algorithm Presentation | `docs/architecture.md`、`docs/experiments.md` |
| **25** | P0 / 3h | 重写 README：问题、算法、基线、实验、失败案例、快速运行、限制；保留上游归属与许可 | 开源 README 与 attribution | GitHub 首页不依赖 UI 也能展示算法 |
| **26** | P0 / 3h | 基于真实贡献撰写 3–4 条简历项目经历，预留**实测**指标位置；整理 2 分钟/5 分钟讲解 | STAR、贡献边界 | `docs/resume_notes.md` |
| **27** | P1 / 3h | 模拟面试：RRF、Graph Path、Confidence、Decision、Ablation、Latency、失败结果等至少 20 问 | 回读关键代码与实验报告 | 面试问答记录；能现场解释两条典型 Trace |
| **28** | P0 / 2h | 重新运行关键命令和指标；检查 API Key、Git 状态、上游许可证；整理最终 Release | 复现审计、差异归因 | 最终版本标签/Commit、交付清单和局限说明 |

### 4.1 每周交付物总结

- **W1**：原版运行记录、Neo4j/索引统计、初版 Benchmark、Baseline 指标。
- **W2**：最小正确图路径、Evidence 适配、Controller 和单测。
- **W3**：正式对照、四组消融、成本分析、失败案例及置信区间。
- **W4**：可复现脚本、README、演示、简历和面试材料。

## 5. 按需学习清单：知识缺口 → 资料 → 实践 → 验证

| 知识 | 为什么学 / 深度 | 学习资料 | 在代码中实践 | 如何证明理解 |
| --- | --- | --- | --- | --- |
| BM25 + RRF | 理解不同召回分数不兼容，能手算小例子 | [RRF 原始论文](https://doi.org/10.1145/1571941.1572114)；`hybrid_retrieval.py` | 固定 Query 比较 BM25/Dense/RRF | 面试解释融合原因和同源去重 |
| BGE + HNSW | 理解 Embed、COSINE、ANN Recall/Latency | [Milvus HNSW](https://milvus.io/docs/hnsw.md)、[BGE](https://huggingface.co/BAAI/bge-small-zh-v1.5) | 实验不同 `ef` 或测初始索引时间 | 能解释近似召回损失 |
| Graph Path / Cypher | 必须避免假路径，理解 1/2 跳和关系方向 | [Neo4j Cypher](https://neo4j.com/docs/cypher-manual/current/) | `graph_rag_retrieval.py` 修复与验证 | 输入一条 Gold，展示真实节点/边 |
| Agent 决策 / State | 区分单次分类与反馈式控制 | [ReAct](https://arxiv.org/abs/2210.03629)、[Adaptive-RAG](https://aclanthology.org/2024.naacl-long.389/) | `evidence_controller.py` | 解释策略切换和最大预算 |
| 证据质量 | 停止不能靠随意置信度 | [CRAG](https://arxiv.org/abs/2401.15884)、[Self-RAG](https://arxiv.org/abs/2310.11511) | Entity/Relation Coverage | 构造“两个实体都有但无共同关系”反例 |
| 数据与评测 | 不能仅用 LLM judge/示例输出 | [BEIR](https://github.com/beir-cellar/beir)、[RAGBench](https://arxiv.org/abs/2407.11005) | Gold、Recall、F1、消融与 Bootstrap | 解释测试泄漏及负面结果 |
| Python 测试与 Trace | 策略在无在线依赖时也要可测 | [pytest](https://docs.pytest.org/) | Mock 检索工具、记录状态转换 | 不连数据库仍能复现控制器测试 |

不必提前学习 LoRA、QLoRA、RLHF、GRPO、全套 LangGraph、Multi-Agent；这些不直接支撑本轮核心假设。

## 6. 三档交付和风险降级

| 等级 | 必须包含 | 不要求 |
| --- | --- | --- |
| **MVP（必须）** | 可追溯 Baseline；修复必要路径或限制不支持功能；80–100 条核验过的 Gold；至少两种检索对照；运行文档与失败分析 | 新控制器显著提升或完整端到端 Agent |
| **推荐** | 200 条目标 Benchmark；证据反馈控制器；B3/B4/Ours 实测；4 个消融；延迟/调用数/Token 统计；项目演示与简历 | 微调和复杂生产服务 |
| **可选进阶** | 所有主线完成后，二选一：Cross-Encoder Reranker / 小型策略分类器 | 两个都做 |

### 风险与明确降级条件

- **Day 3：Neo4j/Milvus/API 启不起来**：保存日志和原始环境信息；优先确保 Neo4j + 本地检索/Mock 可跑，不假装 Baseline 已复现。
- **Day 10：图检索修复超时**：限制为可验证的 Recipe–Ingredient、Ingredient–Recipe–Category 等 1–2 跳查询；明确不支持类型。
- **Day 7：数据集质量不达标**：缩小规模，优先标注质量与人工复核。
- **Day 17：反馈算法效果不好**：先分析错误判停、Graph 检索空结果、实体归一化问题，不通过堆框架掩盖失败。
- **API / GPU 资源不足**：将所有方法切换到同一个固定兼容模型、缓存不变的请求；先做检索阶段客观实验。
- **Day 21：仍未完成核心实验**：停止新增功能，确保 MVP 指标、README、测试和限制说明。

## 7. GitHub、简历与面试验收

### 7.1 GitHub README 要展示

1. 问题背景与**原仓库归属**；2. 原架构 vs 改造架构；3. 证据评分与工具策略；4. Baseline 与 Ours 实测表；5. Gold 构建过程；6. 一键复现与 Demo；7. 消融与失败案例；8. 限制、依赖及许可。

### 7.2 简历描述（仅完成后填写）

- 设计并实现 [已验证的] Evidence-Guided 检索反馈与预算控制器。
- 修复并验证 [实际完成的] 一/两跳图证据路径，维护节点与关系来源追溯。
- 构建 [实际样本数] 条带 Gold 支持路径的 Benchmark，实现 [实际完成指标] 评测。
- 对比 [实际运行方法]，在 [实际测试集] 上获得 [真实质量、成本和延迟数字]，完成 [真实消融和失败分析]。

**禁止**在实验前填写“提升 XX%”；不能把上游 BM25/RRF、Milvus 或原 GraphRAG 算成个人原创贡献。

### 7.3 面试自查（至少 20 问，按真实实现准备）

**基础**：1 原架构？2 Neo4j 与 Milvus 的角色？3 BM25/Dense 区别？4 RRF 公式与去重？5 HNSW 参数？6 Hybrid 与 Combined 区别？7 图路径如何表示？8 Router 置信度可信吗？

**进阶**：9 为什么不能只判断非空？10 为什么是反馈式 Agentic Retrieval？11 与 ReAct 有何异同？12 与 Adaptive-RAG 有何不同？13 如何定义充分性？14 如何避免重复工具调用？15 如何设置预算？16 什么是共享修复 Baseline？

**深入**：17 评分误判怎么办？18 Graph 与文本证据冲突怎么办？19 图合成 Gold 是否偏向 Graph？20 生成幻觉与检索错误如何区分？21 Always-Combined 更准确怎么办？22 效果没有提升怎么办？23 扩大 100 倍如何控制图遍历成本？24 如何从规则控制器发展到学习式策略？

### 7.4 最终通过标准

- [ ] 来源 Commit、许可证和个人新增代码边界清晰。
- [ ] 新环境按 README 可以执行 Smoke Test；外部 API 依赖明确。
- [ ] 原样 Baseline 与共享修复 Baseline 被分别记录。
- [ ] Gold 答案/支持路径可回查，开发集与测试集严格隔离。
- [ ] Agent 具有可追溯 `state → action → observation → stop` 轨迹和预算边界。
- [ ] 存在真实质量、成本、延迟对照，至少四组消融。
- [ ] 有典型成功和失败分析，不隐瞒负面结果。
- [ ] 面试中能指着源码、路径、公式和实验解释关键选择。

**核心原则**：在 28 天内做透一条有证据的算法主线，先复现、再改造、最后报告；不以调用框架数量评价项目质量。
