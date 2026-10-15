# GraphRAG Baseline 源码分析（Day 1）

> 日期：2026-10-15  
> 阶段：Day 1 / 源码理解与开发基线锁定  
> 项目仓库：[Evidence-Guided-Agentic-GraphRAG](https://github.com/xxiiiixx/Evidence-Guided-Agentic-GraphRAG)  
> 上游仓库：[Datawhale / all-in-rag](https://github.com/datawhalechina/all-in-rag)  
> 当前工作仓库初始 Commit：`cbe0b8a2c9e5290ee83ef7dd0a58cb7d00148c9f`  
> 上游 C9 来源 Commit：`cec956e80ae28d6c0ca84c9d39dddcf61ad633fd`

## 0. 状态与证据边界

- **[已读源码]** 已通过 GitHub 检查 `main.py`、`config.py`、`rag_modules/` 中的路由、混合检索、图检索、生成等文件，以及 C9 数据导入脚本。
- **[版本验证]** 本仓库核心代码与上游 C9 对应文件保持相同 Git Blob（抽取阶段）。
- **[用户自述]** Day 1 本地环境检查步骤已完成；尚未将本地命令输出或环境版本保存到本仓库，不能视为已核验可复现环境。
- **[未验证]** 未在本次源码审查中实际启动 Neo4j、Milvus、模型 API 或运行端到端 Benchmark；没有准确率、时延、显存或成本结论。
- 所有性能缺陷、优化收益和算法改动均应在 Day 2–21 的真实实验中确认，不能提前写成已取得的成绩。

## 1. 原始项目目标

原始 C9 是面向**菜谱与烹饪知识**的 GraphRAG 问答示例。它组合：

1. Neo4j 保存 Recipe、Ingredient、CookingStep、Category 等节点与关系；
2. 图数据转菜谱文档并分块，建立向量索引；
3. 混合检索（实体/主题、向量、BM25）以及图路径检索；
4. LLM 分析 Query，选择 Hybrid、Graph 或 Combined；
5. 将检索文档作为上下文交给生成模型回答。

它不是完全独立的通用 Agent，也不是 Microsoft GraphRAG 全部算法的复现。

## 2. 真实模块、函数与职责

| 源文件 | 类 / 关键方法 | 作用 |
| --- | --- | --- |
| `main.py` | `AdvancedGraphRAGSystem.initialize_system()` | 创建数据、索引、生成、两个检索器和路由器；一些模块构造过程中会尝试连接服务/加载模型 |
| `main.py` | `build_knowledge_base()` | 检查/加载 Milvus 集合，加载 Neo4j、生成菜谱文档、切块；缺少索引时构建 |
| `main.py` | `_initialize_retrievers()` | 初始化 Hybrid 和 Graph 检索器 |
| `main.py` | `ask_question_with_routing()` | 在线 Query → 路由检索 → 非空判断 → 生成回答 |
| `rag_modules/graph_data_preparation.py` | `GraphDataPreparationModule` | 读取 Neo4j；构建菜谱文档、章节/字符分块 |
| `rag_modules/milvus_index_construction.py` | `MilvusIndexConstructionModule` | BGE Embedding、Milvus HNSW/COSINE 索引和检索 |
| `rag_modules/hybrid_retrieval.py` | `HybridRetrievalModule.hybrid_search()` | Dual-level + Dense + BM25 召回，RRF 融合 |
| `rag_modules/graph_rag_retrieval.py` | `GraphRAGRetrieval.graph_rag_search()` | LLM 生成图查询意图、执行图路径或子图搜索 |
| `rag_modules/intelligent_query_router.py` | `IntelligentQueryRouter.analyze_query()` | LLM Query 类型和策略分析，失败时规则降级 |
| `rag_modules/intelligent_query_router.py` | `route_query()` | 根据一次性判断调用 Hybrid / Graph / Combined |
| `rag_modules/generation_integration.py` | `generate_adaptive_answer()` | 拼接 Context 和 Question，通过模型 API 生成文本 |
| `config.py` | `GraphRAGConfig` | Neo4j/Milvus/Embedding/LLM/检索与生成配置 |

### 离线数据流

```text
data/C9/cypher/*.csv
  → Neo4j 图数据库
  → GraphDataPreparationModule.load_graph_data()
  → build_recipe_documents()
  → chunk_documents()
    ├─ BGE Embedding → Milvus HNSW
    ├─ jieba + BM25
    └─ 图实体/关系 KV 索引
```

### 在线数据流

```text
Question
  → IntelligentQueryRouter.analyze_query()
  → route_query()
    ├─ HYBRID_TRADITIONAL → hybrid_search()
    ├─ GRAPH_RAG          → graph_rag_search()
    └─ COMBINED           → _combined_search()
  → relevant_docs
    ├─ 为空：直接提示未找到信息
    └─ 非空：generate_adaptive_answer(question, relevant_docs)
  → Answer
```

## 3. Day 1 阅读成果：路由机制

- `HYBRID_TRADITIONAL` 调用 `traditional_retrieval.hybrid_search(query, top_k)`。
- `GRAPH_RAG` 调用 `graph_rag_retrieval.graph_rag_search(query, top_k)`。
- `COMBINED` 调用 `_combined_search()`：给两路分配候选数、交替取结果并去重，**不是**统一精排。
- 注意 `hybrid_search()` 内部本来就有基于排名的 RRF；不要把 Combined 的 round-robin 与 Hybrid 内部的 RRF 混为一谈。
- LLM 返回的 `confidence` 是模型生成的自评分数，未经过校准，不可解释成真实正确概率。
- `_rule_based_analysis()` 是 **LLM 分析异常时的降级逻辑**，不是正常路径唯一判断方式。比如对“鸡肉和土豆可以一起做什么菜？”这句没有空格的 Query，预置复杂度/关系关键词均未命中，分数为零，最终选 Hybrid；`len(query.split())` 得到 1，也不等于实际实体个数。

## 4. 源码级证据与问题诊断

| 观察 | 源码位置 | 证据状态 | 为什么需要验证 |
| --- | --- | --- | --- |
| 检索后缺少明确的证据充分性判定 | `main.py::ask_question_with_routing` | 已确认 | 非空文档会进入生成，但文档不一定共同支持问题条件 |
| 正常低质量结果不会触发另一路补检索 | `intelligent_query_router.py::route_query` | 已确认 | 只在检索异常时回退 Hybrid；这不等于证据反馈 |
| 图路径方法部分为空实现 | `graph_rag_retrieval.py::_find_entity_relations/_find_shortest_paths` | 已确认 | 两个方法直接 `return []`，特定查询类型可能无结果 |
| 推理链“验证”是占位逻辑 | `graph_rag_retrieval.py::_build_reasoning_chain/_validate_reasoning_chains` | 已确认 | 生成模板文本并切片，不是图事实校验 |
| Prompt 要求解析的 constraints 未写入 GraphQuery | `graph_rag_retrieval.py::understand_graph_query` | 已确认 | 时间/健康等约束可能未进入后续结构化路径 |
| 变长路径后的关系列表与转换过程可能不一致 | `graph_rag_retrieval.py::extract_knowledge_subgraph/_build_knowledge_subgraph` | 合理风险 | Day 3 需要用真实 Cypher/记录验证具体表现 |
| Graph 路径证据缺少充分的结构化来源信息 | `graph_rag_retrieval.py::_paths_to_documents` | 已确认 | 缺少完整节点/边身份及方向，不利于可核验引用 |
| RRF 已有候选去重和来源排名记录 | `hybrid_retrieval.py::_rrf_merge` | 已确认，值得保留 | 不应把现有 RRF 或父文档回填当成个人原创改进 |

### 示例：关键词命中 ≠ 关系证据充分

问题：“哪道菜同时包含鸡肉和土豆？”

- 文档 A：某道菜包含鸡肉；
- 文档 B：另一道菜包含土豆；
- 这两条证据分别覆盖了两个实体，却不能证明**同一道菜同时包含两种食材**。

所以不能只判断 `relevant_docs` 是否为空，甚至不能只检查两个关键词是否都出现；还需要检查关联实体、关系以及支持路径。

## 5. 我的理解与疑问（Day 1 学习记录）

> 我的理解：原始系统会依据 Question 进行路由检索，但拿到文档后，缺少对结果进行质量评估并决定“是否保留、是否换工具继续检索、是否停止”的反馈环节。仅有 `relevant_docs` 并不意味着证据质量足够。
>
> **进一步精确化：** 不是一定需要 LLM 自由“反思”，可以先使用可计算的实体覆盖、关系路径校验和预算规则来作决策。检索抛异常时的 fallback 与正常返回低质量证据后的补检索，是两类不同问题。
>
> 待学习/核验：图路径支持程度如何自动评分？Hybrid 结果和 Graph 路径如何统一 Evidence ID？规则评分是否会过早停止？本地环境版本和运行日志仍待记录。

## 6. 暂定改造方向（不是已经完成的功能）

**Evidence-Guided Budget-Aware Adaptive Retrieval**：

1. 根据 Query 选择一个初始检索工具；
2. 标准化已返回的文本证据和图路径；
3. 检查实体覆盖、关系证据、有效路径与重复程度；
4. 证据不足且预算允许时，调用另一检索工具；
5. 充分时停止并回答，预算耗尽时谨慎回答或拒答；
6. 为每个决策保存 trace，并与一次性路由、固定双路检索公平比较。

**算法主线一条**：证据反馈的动态检索与停止决策。辅助工作为图路径证据最小正确性修复、Benchmark/评测；不强行扩展到 Multi-Agent、SFT 或重新训练图模型。

## 7. Baseline 与实验原则

- 原始代码 **As-Is Baseline** 与 **共同 Bug 修复后的 Baseline** 必须分开记录，避免把修复已有缺陷当成控制器提升。
- 固定 Query、Gold、索引、模型、生成参数、检索候选数和工具预算。
- 需要客观指标：Evidence Recall@K、Path Validity、Answer F1/EM、拒答准确率、工具调用、延迟与 Token 使用量。
- Day 1 **没有实验数据**；不得提前写性能收益。

## 8. Day 2 下一步

1. 记录 `python --version`、`docker --version`、`docker compose version`、GPU 信息和 git SHA；
2. 启动 `data/C9/docker-compose.yml` 中的 Neo4j，检查健康与导入日志；
3. 执行 `MATCH (n) RETURN labels(n), count(*)` 等统计查询；
4. 采样 `Recipe-REQUIRES-Ingredient`、`Recipe-CONTAINS_STEP-CookingStep` 的真实路径，记录节点/关系 ID；
5. 记录所有失败信息，**不要**在 Baseline 评测前随意更改算法逻辑。

## 9. 上游与许可

原始源码和数据来自 Datawhale/all-in-rag；仓库 `UPSTREAM.md` 和原项目 README 指向 CC BY-NC-SA 4.0。保留原始归属及非商业/相同方式共享义务，所有个人新增实现必须与上游区分。
