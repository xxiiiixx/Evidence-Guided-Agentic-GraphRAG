# Day 2 · 2026-10-21 · 配置环境变量化 + CLI 入口 + Milvus Lite

> 状态：**通过**。改 `.env` 即可换模型/端点，`--question/--batch/--mode` 可用，
> 端到端跑通 1 条，`--mode hybrid` 召回目标菜谱 rank 1。

## 1. 这一天做了什么

| 层 | 改动 | 产物 |
| --- | --- | --- |
| 配置 | 全部参数改读环境变量，未设置回退默认 | `config.py` |
| 模型接入 | `base_url` / `api_key_env` 可配置，不再写死 Moonshot | `generation_integration.py` |
| 检索分派 | 新增 `search_with_strategy()`，强制走指定策略 | `intelligent_query_router.py` |
| 入口 | `argparse` 替换交互式 main，加 4 个参数 | `main.py` |
| 向量库 | 支持 Milvus Lite，无需 Docker | `milvus_index_construction.py` |
| 可复现 | 每次运行落配置快照 | `results/config_snapshot.json` |

## 2. CLI 设计

```bash
python main.py -q "咖喱炒蟹怎么做？" -m hybrid
```

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `-q/--question` | 字符串 | 单条，跑完退出 |
| `-b/--batch` | 文件路径 | `.jsonl` 或 `.txt`，每行一个问题 |
| `-m/--mode` | `auto`/`hybrid`/`graph`/`combined` | 见下表 |
| `--top-k` | 整数 | 覆盖配置里的 top_k |
| `--limit` | 整数 | 批量模式最多跑几条 |
| `--trace` | 路径 | trace 落盘，非交互模式默认 `results/smoke_trace.jsonl` |
| `--no-build` | flag | 跳过知识库构建（已建过时用） |

`-q` 与 `-b` 互斥；不带任何参数时保持原交互式行为。

**`--mode` 直接继承代码里的枚举**（`intelligent_query_router.py:19`），不另造一套映射：

| mode | 行为 | 实验编号 |
| --- | --- | --- |
| `auto` | 走 LLM 路由判断用哪条路 | 现有默认行为 |
| `hybrid` | 强制仅传统混合检索 | **B1** |
| `graph` | 强制仅图检索 | **B2** |
| `combined` | 强制两路合并 | **B4** |

> `adaptive`（Ours）留到 Day 11 controller 写完再加第五个值。
> `search_with_strategy()` 与 `route_query()` 的区别：后者调 LLM 决定路线，
> 前者强制走指定路线——**对照组必须能绕过 LLM，否则每条 baseline 都在测 LLM 的判断**。

## 3. 环境变量

`.env` 已被 `.gitignore` 忽略，仓库里只有 `.env.example`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODELSCOPE_API_KEY` | 空 | 魔搭 token |
| `LLM_BASE_URL` | `https://api-inference.modelscope.cn/v1` | OpenAI 兼容端点 |
| `LLM_MODEL` | `Qwen/Qwen3.8-Flash-Next` | 见 §5 的坑 |
| `LLM_API_KEY_ENV` | `MODELSCOPE_API_KEY` | 密钥所在的变量名 |
| `MILVUS_LITE_PATH` | `./milvus_data.db` | 非空走 Lite；留空则连 host:port |
| `NEO4J_URI` / `USER` / `PASSWORD` | `bolt://localhost:7687` / `neo4j` / `all-in-rag` | |

`to_dict()` 默认把 `neo4j_password` 替换为 `***`，配置快照可直接入库不泄露。

## 4. Milvus 从 Docker 改为 Lite

**为什么改**：Milvus standalone 容器在服务器上 panic（Go runtime），
且 322 道菜 / 约 3000 chunk 用独立服务属过度设计。

`MilvusClient(uri=...)` 本身支持本地文件，改造成本极低：

```python
self.is_lite = not self.uri.startswith(("http://", "https://", "tcp://"))
```

### 三个必须处理的坑

**① 环境变量名撞车**

```
MILVUS_URI=./milvus_data.db  ->  import pymilvus 直接崩
ConnectionConfigException: Illegal uri: [./milvus_data.db],
expected form 'http[s]://[user:password@]example.com[:12345]'
```

根因：`pymilvus/orm/connections.py:142` 在**模块 import 阶段**读取 `MILVUS_URI`
并强制校验 http(s) 格式。用同名变量传本地路径，库还没加载完就抛异常。
→ 改名为 `MILVUS_LITE_PATH`，并在 import pymilvus 之前清理残留的非法 `MILVUS_URI`。

**② 索引类型不兼容**

```
create_index 报 code=65535
invalid index type: HNSW, local mode only support FLAT IVF_FLAT AUTOINDEX
```

→ 按模式分支：Lite 用 `FLAT`，远程服务保持 `HNSW(M=16, efConstruction=200)`。
搜索时 `ef` 参数仅远程传，Lite 传空 `params`。

**③ 连接自检会误报**

部分 Lite 版本的 proto 与 `list_collections` 不兼容，构造成功后调用仍可能失败。
→ 自检降级为 warning 不阻断：`MilvusClient` 构造成功即视为可用。

> **FLAT 反而更合适**：3000 量级下 FLAT 是精确检索，无近似误差。
> 评测要求结果可复现，HNSW 会引入与判定器无关的召回抖动。

## 5. 踩坑：魔搭上并非所有模型都有 provider

首次运行报 400：

```
Model id : Qwen/Qwen2.5-72B-Instruct , has no provider supported
```

**魔搭推理 API 是聚合平台**，模型列表里有不代表能调。实测 `Qwen/Qwen3.8-Flash-Next` 可用。

> 教训：换模型前先在平台上确认有可用 provider，不要看模型广场有名字就写进配置。

## 6. 实测结果

运行命令：

```bash
cd data/C9 && docker compose up -d          # 起 Neo4j
python main.py -q "咖喱炒蟹怎么做？" -m hybrid
```

| 指标 | 实测 |
| --- | --- |
| 模式 | `hybrid`（B1，仅传统检索） |
| 召回文档数 | **5** |
| Top-1 | **咖喱炒蟹**（`node_id=201000001`），即目标菜谱 ✅ |
| 检索耗时 | **14 815 ms** |
| 端到端耗时 | **32 590 ms** |
| 生成模型 | `Qwen/Qwen3.8-Flash-Next` |
| 回答长度 | 1063 字符 |

召回列表：

| rank | 菜谱 | node_id | RRF 分 | 来源 |
| ---: | --- | --- | ---: | --- |
| 1 | 咖喱炒蟹 | 201000001 | 0.032787 | dual_level |
| 2 | 微波葱姜黑鳕鱼 | 201000023 | 0.016129 | dual_level |
| 3 | 姜葱捞鸡 | 201002434 | 0.016129 | bm25 |
| 4 | 洋葱 | 201000004 | 0.015873 | dual_level |
| 5 | 卤菜 | 201002224 | 0.015873 | bm25 |

**分数可以反推融合行为**。RRF 公式 `score = Σ 1/(k + rank)`，`k=60`：

```
0.032787 ≈ 2 × 1/(60+1)   ← 被 2 个源同时命中
0.016129 ≈ 1 × 1/(60+2)   ← 单源命中
```

即：**咖喱炒蟹被两个检索源同时命中并置于首位，其他菜只有单源**。
这是 RRF 融合生效的直接证据，不是"看起来排第一"。

两侧都出现 `dual_level` 与 `bm25`，说明 dense 与 sparse 都在工作。

## 7. 链路的每一环

```
Neo4j（5803 节点 / 20248 关系）
  └─ GraphDataPreparationModule: 322 菜谱 → 文档 → 分块（322 文档，约 3000 chunk）
       └─ BGE-small-zh（512 维，CPU）→ Milvus Lite（FLAT 索引）
            └─ HybridRetrieval: dual_level + bm25 → RRF 融合 → top-5
                 └─ GenerationIntegration: 拼上下文 → 魔搭 API → 回答
```

**已通**：Neo4j → 分块 → embedding → 向量索引 → 混合检索 → RRF → 生成。

## 8. 性能诊断（Day 7 前要解决）

| 阶段 | 耗时 | 占比 | 问题 |
| --- | ---: | ---: | --- |
| 检索 | 14.8 s | 45% | **过慢**，40 条 × 多方法会拖垮实验 |
| 生成 | 17.8 s | 55% | 云端 API，网络 + 模型推理 |

检索 14.8 s 的主因是 **BGE 在 CPU 上跑**，且每道菜要编码多个 chunk（约 3000 个）。
T4 有 GPU 却没用上——`milvus_index_construction.py` 里 embedding 写死了 `device='cpu'`。

> **Day 3 之后要做的两件事**：
> 1. embedding 改 `device='cuda'`（T4 可用，预期提速 10 倍以上）
> 2. 批量编码而非逐条编码
>
> 按 40 条 dev × 6 个方法估，14.8 s/条会让单轮实验超过 1.5 小时，
> **这是必须在 Day 7 前解决的瓶颈**。

## 9. 复现

```bash
# 1. 起 Neo4j
cd data/C9 && docker compose up -d && cd ../..

# 2. 配置
cp .env.example .env        # 填入 MODELSCOPE_API_KEY
# Milvus 用 Lite 时保持 MILVUS_LITE_PATH=./milvus_data.db

# 3. 冒烟
python main.py -q "咖喱炒蟹怎么做？" -m hybrid

# 4. 产物
#    results/smoke_trace.jsonl     每条问题的完整记录
#    results/config_snapshot.json  运行时配置（密码已脱敏）
```

---

## 10. 面试问答

**Q1：为什么把 `--mode` 做成参数，而不是写死一个最佳策略？**

A：因为**需要对照组**。项目要证明「连通性判定比相似度基线好」，
必须有 B1（仅 hybrid）/ B2（仅 graph）/ B4（combined）各自的独立结果。

如果只留一个 `auto` 路由，所有对比都混进了 LLM 的路线判断，
**分不清收益来自检索策略还是路由决策**。

更关键的是 `search_with_strategy()` 绕过了 LLM——对照组不能依赖被测对象本身来做决策。

**Q2：Milvus Lite 和 standalone 的差别，为什么 Lite 反而更适合？**

A：三点差异：

1. **部署**：Lite 是嵌入式（进程内文件），standalone 要独立容器。3000 向量不值得起服务。
2. **索引**：Lite 只支持 FLAT/IVF_FLAT/AUTOINDEX，不支持 HNSW；standalone 支持 HNSW。
3. **精度**：FLAT 是**精确检索**（暴力算距离），HNSW 是**近似检索**（图索引 + ef 参数）。

对评测来说 FLAT 更好：**HNSW 的近似误差会引入与判定器无关的召回抖动**，
而我要测的是判定器效果，不想要索引带来的随机性。

代价是 FLAT 复杂度 O(n)，但 3000 条 × 512 维，GPU/CPU 都能秒回。

**Q3：14.8 秒的检索慢在哪，你怎么定位的？**

A：拆成两段——`retrieved_ms` 与 `latency_ms`（总）：
检索 14.8 s、生成 17.8 s，说明瓶颈在检索而非网络。

再看检索内部：`hybrid` 模式走 dual_level + bm25 + RRF。
bm25 是纯 CPU 文本匹配，毫秒级；dual_level 要调 embedding。
而代码里 `HuggingFaceEmbeddings(model_kwargs={'device': 'cpu'})` —— **T4 闲置，BGE 在 CPU 上跑**。

约 3000 个 chunk 逐条编码，CPU 上 14 s 是合理量级。修复方向明确：改 `device='cuda'` + 批量编码。

> 这里体现的是：**性能问题要先分段测量再下结论**，不能看到"慢"就直接换模型。

**Q4：`.env` 里的密钥怎么保证不泄露？**

A：三道防线：

1. `.gitignore` 第一行就是 `.env`，`git check-ignore` 可验证
2. 仓库里只有 `.env.example`，值是占位符
3. `to_dict(mask_secrets=True)` 在配置落盘时把密码替换为 `***`，
   所以 `results/config_snapshot.json` 可以直接入库

> 反例：`milvus_test.db` 曾被误提交进仓库（Day 1 之后），因为 `.gitignore`
> 只写了 `milvus_data.db` 没覆盖测试文件。**忽略规则要用通配符，不要逐文件列举。**
