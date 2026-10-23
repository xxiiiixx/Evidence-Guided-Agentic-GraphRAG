# Evidence-Guided Agentic GraphRAG

> 一个**可验证**的检索智能体：把 Agent 的观测函数从「LLM 自己觉得够了没」换成「证据子图上的连通性判定」，于是每一步决策都能被离线复核。

基于 [Datawhale / all-in-rag](https://github.com/datawhalechina/all-in-rag) 的 C9 GraphRAG 教学项目二次开发，聚焦 Agent / RAG 算法方向的系统性实验。

**当前状态：Day 2 / 28 完成。端到端链路已跑通 1 条（hybrid 模式 Top-1 命中），尚无自建 Benchmark 与对比指标。** 本仓库在实验完成前不会出现任何未经运行的指标。

---

## 问题

RAG Agent 的核心动作是「检索 → 判断够不够 → 决定继续还是停」。现在的判断通常有两种做法：

1. **`top_k` 非空就停** —— 最朴素，但在多实体关联问题上系统性假阳性
2. **LLM 自评「证据是否充分」** —— Self-RAG / CRAG 一类，灵活但**不可验证、不可复现、有额外成本**

用一个具体反例说明为什么二者都不够：

> 问：「哪道菜同时包含鸡肉和土豆？」
> 证据 A：某道菜含鸡肉。证据 B：另一道菜含土豆。
> 两个关键词都命中，`relevant_docs` 非空，LLM 也可能说「够了」——**但没有任何一道菜同时含两者**。

关键词覆盖与文本相似度都无法表达「多条证据**共同**支持一个多跳结论」。

---

## 方法

### 核心 insight

> **充分性不是相似度问题，是连通性问题。**

### 形式化定义

给定查询 `q`，抽取必需实体集 `E_q` 与必需关联集 `R_q ⊆ E_q × E_q`；给定已检索证据 `S`（每条证据携带 `node_ids` / `edge_ids`）。称 `S` 对 `q` **充分**，当且仅当：

1. **实体覆盖**：`E_q ⊆ ⋃_{s∈S} nodes(s)`
2. **连通覆盖**：`∀(e_i, e_j) ∈ R_q`，`S` 诱导子图中 `e_i` 与 `e_j` 连通
3. **预算合规**：`cost(S) ≤ B`

判定只需一次 BFS，`O(|V|+|E|)`，**零 LLM 调用**。

### 与已有方法的区别

| 方法 | 充分性判据 | 可证明多跳结论 | 可离线复核 | 额外 LLM 成本 |
| --- | --- | --- | --- | :---: |
| 朴素 RAG | `top_k` 非空 | 否 | — | 0 |
| Self-RAG | LLM 生成 relevance token | 否（概率性） | 否 | 有 |
| CRAG | 分类器 correct/incorrect/ambiguous | 否（文本层） | 部分 | 有 |
| **本项目** | 子图连通性 + 覆盖 | **是（确定性）** | **是** | **0** |

「可离线复核」是关键性质：给定一条 Agent trace，任何人可以在不调用模型的情况下重算当时每一步的判定，因此**决策过程可审计、可复现**。

### 已知边界

这是**必要条件，不是充分条件**。文本能推出、但图里没有连通的证据会被判为「不足」，产生 false negative —— 表现为多花一次检索，而非答错。项目包含图扰动实验（随机删边 10/20/30%）来量化这一退化。

---

## 项目状态

| 周 | 时间 | 目标 | 状态 |
| --- | --- | --- | --- |
| W1 | Day 1–7 | 服务器环境、图检索修复、Benchmark 40 条 | 🔄 Day 2 |
| W2 | Day 8–14 | 判定器 + 控制器 + Agent 决策指标 | ⬜ |
| W3 | Day 15–21 | 对照实验、六组消融、鲁棒性 | ⬜ |
| 缓冲 | Day 22–28 | 复现、README、面试打磨 | ⬜ |

**当前已有的**：

- 上游 C9 源码（`main.py`、`config.py`、`rag_modules/`）与 Neo4j 导入数据（`data/C9/`）
- 28 天计划（[docs/28_day_plan.md](./docs/28_day_plan.md)）
- Day 1 环境与数据库自检脚本（`scripts/`）；环境已验证（T4 15GB，Ubuntu 24.04，Python 3.10.22）
- Neo4j 已落地：5803 节点 / 20248 关系（[Day 1 记录](./docs/day01.md)）
- 端到端链路已通：Neo4j → 分块 → BGE → Milvus Lite → hybrid 检索 → 生成（[Day 2 记录](./docs/day02.md)）
- 检索侧的 LLM 抽词已换成图谱词表匹配（[Day 4 记录](./docs/day04.md)）

**当前没有的**：自建 Benchmark、对比指标、消融结果。**尚无性能结论。**

---

## 快速开始

> 以下命令尚未在本仓库验证。Day 1 验证通过后会更新本节。

### 环境要求

- Linux（当前开发环境为实验室服务器）
- Docker + Docker Compose
- Python 3.10 / 3.11（**不要用 3.13**，`torch==2.6.0` 支持不稳）
- NVIDIA GPU 16 GB（本地跑 Qwen2.5-7B 4bit；无 GPU 可降级到 3B）

### 启动数据库

```bash
cd data/C9
docker compose up -d          # Neo4j 5.18，自动执行 cypher/neo4j_import.cypher
docker compose ps             # 等 neo4j-db healthy、neo4j-init exit 0
```

### 自检

```bash
bash scripts/day01_env_check.sh      # 环境快照
bash scripts/day01_db_check.sh       # 数据库统计 → results/neo4j_stats.json
```

### Python 环境

```bash
conda create -n graphrag python=3.10 -y && conda activate graphrag
pip install -r requirements.txt
cp .env.example .env                 # 填入 MODELSCOPE_API_KEY
python main.py -q "咖喱炒蟹怎么做？" -m hybrid
```

LLM 走 OpenAI 兼容端点（默认魔搭），换成本地 vLLM 只改 `.env` 的
`LLM_BASE_URL` 与 `LLM_MODEL`，不动代码。

向量库默认用 **Milvus Lite**（本地文件，无需 Docker）；置空
`MILVUS_LITE_PATH` 即回落到 `MILVUS_HOST:MILVUS_PORT` 的独立服务。

**安全提示**：`data/C9/docker-compose.yml` 里的 Neo4j 密码是上游演示值，仅适合隔离的本地环境，不要暴露公网。

---

## 仓库结构

```
config.py                    配置（环境变量驱动，支持端点/模型切换）
main.py                      入口，AdvancedGraphRAGSystem；CLI 支持 -q/-b/-m
rag_modules/
  graph_data_preparation.py  Neo4j → 菜谱文档 → 分块
  milvus_index_construction.py  BGE embedding + Milvus（Lite 用 FLAT，远程用 HNSW）
  hybrid_retrieval.py        Dual + Dense + BM25 → RRF
  graph_rag_retrieval.py     图查询意图 → 图路径 / 子图
  intelligent_query_router.py 一次性路由（Baseline）
  graph_indexing.py          图实体/关系索引
  generation_integration.py  上下文拼接 + 生成
  graph_vocabulary.py        图谱名单（提示词用的真实名字，本身不做匹配）
  llm_entity_extractor.py    闭集实体抽取（名单进提示词 + 输出校验）
data/C9/                     Neo4j compose、Cypher 导入、CSV 数据（上游原样）
data/rebuild/                Day 3 重建图：CSV + 精简导入脚本 + 独立 compose + 自检脚本
scripts/                     Day 1 自检脚本、Day 3 图重建脚本、Day 4 抽词评测
tests/                       单元测试（stdlib unittest）
docs/
  28_day_plan.md             28 天计划（只写计划，不记执行过程）
  day01.md                   Day 1 执行记录（环境、数据基线、面试问答）
  day02.md                   Day 2 执行记录（配置、CLI、Milvus Lite、实测）
  day03.md                   Day 3 执行记录（图重建、导入、数据质量问题）
  day04.md                   Day 4 执行记录（实体链接、消歧、5 处静默失效）
```

---

## 文档

- [28 天计划](./docs/28_day_plan.md) —— 28 天逐日安排：任务、产物、验收（只写计划，不记执行过程）
- [Day 1 记录](./docs/day01.md) —— 环境快照、数据库基线、数据质量问题、面试问答
- [Day 2 记录](./docs/day02.md) —— 配置环境变量化、CLI 设计、Milvus Lite 三个坑、实测结果
- [Day 3 记录](./docs/day03.md) —— 图重建（连通性 0 → 255）、导入脚本重写、发现的数据缺陷
- [Day 4 记录](./docs/day04.md) —— 闭集实体抽取（名单进提示词，从「生成」变「选择」）、走过的确定性匹配弯路、七处静默失效

每日记录独立存放，不与计划文档混写。

---

## 数据

C9 菜谱知识图谱：菜谱、食材、烹饪步骤、分类，以及它们之间的关系。

| 节点 label | 数量 | 关系 | 数量 |
| --- | ---: | --- | ---: |
| Ingredient | 2906 | REQUIRES（Recipe→Ingredient） | 2905 |
| CookingStep | 2514 | CONTAINS_STEP（Recipe→CookingStep） | 2514 |
| Recipe | 323 | BELONGS_TO（Recipe→RecipeCategory） | 314 |
| RecipeCategory | 9 | DIFFICULTY_LEVEL（Recipe→DifficultyLevel） | 322 |
| DifficultyLevel | 5 | | |
| Root / CookingMethod / CookingTool | 各 1 | | |

> 以上为 CSV 统计值。数据库实际导入已与之一致（[Day 1 记录](./docs/day01.md)），
> 数据库总数为 5803 节点 / 20248 关系，多出部分为导入脚本自动派生的结构。
> 计数含 4 个类型元节点（`200000000` 菜谱、`300000000` 食材等），故 `Ingredient 2906` 比实际食材数多 1。

选择该数据集的理由：实体集封闭（可用确定性实体链接，排除 NER 误差干扰）、图结构真实可回查、规模允许在 21 天内跑完全部对照实验。

---

## 上游来源与许可

- 来源：[datawhalechina/all-in-rag](https://github.com/datawhalechina/all-in-rag)
- 上游 Commit：[`cec956e80ae28d6c0ca84c9d39dddcf61ad633fd`](https://github.com/datawhalechina/all-in-rag/commit/cec956e80ae28d6c0ca84c9d39dddcf61ad633fd)
- 抽取范围：`code/C9/` 主要运行文件、`data/C9/` 数据库导入与部署文件
- 原仓库声明 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans)：**非商业、相同方式共享、保留署名**。详见 [UPSTREAM.md](./UPSTREAM.md)
- 原项目版权归 Datawhale 及原贡献者；本仓库的后续改动与评测会明确区分原始实现与个人修改。**BM25 / RRF / Milvus / 原 GraphRAG 实现均不属于本项目贡献。**
