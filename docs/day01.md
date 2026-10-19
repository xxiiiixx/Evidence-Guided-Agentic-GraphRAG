# Day 1 执行记录 · 2026-10-19

> 状态：**进行中**（本机工作已完成，服务器验证待执行）
> 对应计划：[28_day_plan.md](./28_day_plan.md) Week 1 Day 1
> 原则：本文件只记「今天做了什么、发现了什么、还缺什么」，不写计划内容。

---

## 1. 今天的目标

计划里 Day 1 的验收物是两样：

- [ ] `results/neo4j_stats.json` —— 数据库真实计数
- [ ] `docs/env.md` —— 环境版本快照

围绕这两样，今天要完成的事：

1. 服务器环境探测（OS / GPU / Python / Docker / 网络）
2. Neo4j 启动 + C9 数据导入 + 计数
3. 源码通读，确认后续设计的技术前提

---

## 2. 已完成

### 2.1 源码审查（本机，已核实）

通读了 `main.py`、`config.py`、`rag_modules/` 全部 8 个模块（约 3857 行），逐条定位缺陷。三个**架构级**发现如下（详细证据已并入计划 §2，此处只记结论与影响）：

**① 环境变量根本没被消费。**
全仓库 `getenv` 只有 `generation_integration.py:27` 一处，`config.py:81` 的 `DEFAULT_CONFIG = GraphRAGConfig()` 是纯硬编码 dataclass。
→ 影响：`.env` 里除 `MOONSHOT_API_KEY` 外全部无效；换模型必须改代码；**实验无法复现**。
→ 结论：Day 2 必修，这不是优化项，是可复现性的前提。

**② 图检索不是「部分空实现」，是整条链路断的。**
`graph_rag_retrieval.py:500-517` 的分派里，`PATH_FINDING` 和 `ENTITY_RELATION` 两种 query_type 都走到 `return []` 的空方法。
→ 影响：`ENTITY_RELATION` 是最高频类型（「哪道菜含鸡肉和土豆」），**Graph 基线在这类问题上召回率恒为 0**。
→ 结论：实验设计必须改——B2（仅 Graph）在原样代码下无意义；Ours vs B3（共享修复的 One-shot）才是唯一有效对照；修复要提前到 Day 4–5，早于 Baseline 冻结。

**③ 实体识别不需要 NER。**
`intelligent_query_router.py:161` 的 `entity_count=len(query.split())` 对中文恒为 1（bug），但它揭示了正确路径：实体集封闭（2906 Ingredient + 323 Recipe），`synonyms` 字段 520 条非空。
→ 结论：用 Aho-Corasick 多模式匹配即可，零 LLM 成本、可复现、绕开中文分词噪音。这让 Controller 的 `required_entities` 变得廉价可靠。

### 2.2 一个值得复用的设计（面试素材）

`hybrid_retrieval.py:630-700` 的 `_rrf_merge` 处理了同源重复命中：一道菜的多个 chunk 共享 `recipe.nodeId` 时，naive RRF 会给该菜加多次分而霸榜；这段代码按 `node_id` 归并、每个 source 只取最佳 rank，并把命中 chunk 数另存到 `rrf_chunk_hits`。

→ 这个 `rrf_chunk_hits` 天然就是「证据新旧」信号，A2 消融可直接复用，不必新造特征。

### 2.3 本机环境诊断

| 项 | 结果 | 影响 |
| --- | --- | --- |
| Docker | **未安装** | 本机不能跑 Neo4j/Milvus |
| WSL | **未启用** | 同上 |
| 管理员权限 | 无 | 无法安装 Docker Desktop |
| conda | 有，base = Python 3.13.11 | **不可用**（`torch==2.6.0` 对 3.13 支持不稳） |
| D 盘剩余 | 约 87 GB | 够用 |
| HTTPS (curl) | `schannel: SEC_E_NO_CREDENTIALS` | 本机 TLS 异常，git 已改用 OpenSSL 后端并成功克隆 |

→ 结论：**本机只写代码，不跑实验。** 重活全部在实验室 Linux 服务器。

### 2.4 交付物

| 文件 | 说明 | 状态 |
| --- | --- | --- |
| `scripts/day01_env_check.sh` | 环境快照采集（OS/CPU/GPU/Python/Docker/Java/网络/仓库盘点），只读，失败不中断 | ✅ 已写，待服务器验证 |
| `scripts/day01_db_check.sh` | 启动 Neo4j → 等 healthy → 等导入 → 统计节点/关系 → 写 `results/neo4j_stats.json` → 与预期值对比，失败时打印诊断清单 | ✅ 已写，待服务器验证 |
| `README.md` | 重写为标准开源项目结构（问题 → 方法 → 状态 → 快速开始 → 结构 → 数据 → 许可），开头讲清 insight | ✅ 已完成 |
| `docs/28_day_plan.md` | 唯一计划文档 | ✅ 已完成 |

---

## 3. 待执行（服务器侧）

服务器可连、GPU 16 GB、可访问 HuggingFace，以下命令按顺序执行：

```bash
# 1. 环境快照
bash scripts/day01_env_check.sh 2>&1 | tee results/day01_env_check.txt

# 2. 数据库统计
bash scripts/day01_db_check.sh 2>&1 | tee results/day01_db_check.txt
```

需要贴回的原始输出：

- [ ] `nvidia-smi` 的显存总量（标称 16 GB 可能因 ECC / 多卡切分而不够，这决定 7B 是否要降 3B）
- [ ] Neo4j 节点计数表
- [ ] Neo4j 关系计数表
- [ ] `day01_db_check.sh` 第 6 节的「实际 vs 预期」对比表
- [ ] 任何 `[FAILED]` 或 `ERROR` 行的完整上下文

---

## 4. 预期值（用于核对）

按 CSV 统计（`nodes.csv` 5761 行、`relationships.csv` 6056 行，均含表头）：

| 节点 | 预期 | 关系 | 预期 |
| --- | ---: | --- | ---: |
| Ingredient | 2906 | REQUIRES (801000001) | 2905 |
| CookingStep | 2514 | CONTAINS_STEP (801000003) | 2514 |
| Recipe | 323 | BELONGS_TO (801000004) | 322 |
| RecipeCategory | 9 | DIFFICULTY_LEVEL (801000005) | 314 |

> `nodes.csv` 每类第一行是类型元节点（如 `300000000,Ingredient,食材,...`），导入后计数可能差 1，属正常，记录即可。

---

## 5. 服务器输出（待填）

### 5.1 GPU

```
（待贴回 nvidia-smi 输出）
```

### 5.2 节点 / 关系计数

```
（待贴回）
```

### 5.3 差异分析

```
（待填：实际值与预期的差异及原因）
```

---

## 6. 今天的判断与决策记录

| 决策 | 理由 | 影响 |
| --- | --- | --- |
| 本机不装 Docker，不在本机跑实验 | 无管理员权限、WSL 未启用；服务器已有 GPU 与 Docker | 所有实验走服务器；同步用 git |
| Python 用 3.10/3.11，不用 3.13 | `torch==2.6.0` 对 3.13 支持不稳 | 服务器建独立 conda env |
| 图检索修复提前到 Day 4–5 | 不修则 B3 对照无意义，Week 3 全部作废 | 压缩了实体链接器的排期 |
| README 重写而非局部修补 | 原 README 只是文件清单，无法在 30 秒内传达价值 | 已改为「问题 → 方法 → 状态」结构 |

---

## 7. 待确认（不阻塞今天）

1. 服务器能否长期占用 Neo4j 容器（独占还是多人共享）
2. 是否有可用的 API key（E5 模型交叉验证只需在 40 条 dev 上跑一次）
3. Qwen2.5-7B 权重是否已缓存（HF 可达则无需预下，但首次生成会慢）

---

## 8. 明天（Day 2）第一件事

按计划 Day 2，但**前提是 Day 1 的数据库计数通过**：

1. `config.py` 改为从环境变量加载 + `to_dict()` 落盘
2. `main.py` 加 `--question / --batch / --mode` 非交互入口
3. 起 Milvus，跑通 3 个问题的端到端

如果 Day 1 的导入失败，Day 2 改为先解决导入，不并进新任务。

---

## 9. 今日自检

- [x] 今天有可验证产物？（两个脚本 + README，但**没有运行记录**）
- [x] 能对着代码讲 10 分钟？（能讲架构级三条发现）
- [ ] 数据库真实计数已拿到？← 待服务器
- [ ] 环境版本已记录？← 待服务器
- [x] 代码已提交？

> 严格说，今天**没有满足**「每天必须有可验证文件」这条规矩——因为服务器输出还没回来。
> 在拿到 `neo4j_stats.json` 和 `env.md` 之前，Day 1 记为 `[~] 进行中`，不能标 `[x]`。
