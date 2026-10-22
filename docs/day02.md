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
0.032787 ≈ 2 × 1/(60+1)   ← 被 2 个源同时命中并都置首位
0.016129 ≈ 1 × 1/(60+2)   ← 单源命中
0.015873 ≈ 1 × 1/(60+3)   ← 单源命中
```

即：**咖喱炒蟹被两个检索源同时命中并置于首位，其他菜只有单源**。
这是 RRF 融合生效的直接证据，不是"看起来排第一"。

两侧都出现 `dual_level` 与 `bm25`，说明 dense 与 sparse 都在工作。

## 6.1 第二次运行：开启 CUDA 后

改用 `EMBEDDING_DEVICE=cuda` 重跑同一条问题（配置见 `config_snapshot.json`）。

| 指标 | CPU（第 1 次） | CUDA（第 2 次） | 变化 |
| --- | ---: | ---: | ---: |
| 检索 | 14 815 ms | **12 685 ms** | **−14.4%** |
| 端到端 | 32 590 ms | 37 544 ms | +15.2% |
| 回答长度 | 1063 字符 | 1627 字符 | +53% |
| Top-1 | 咖喱炒蟹 | 咖喱炒蟹 | 不变 |

**检索只快了 14.4%，远低于预期的 10 倍。** 这推翻了我原先"改成 cuda 就能解决"的判断，
原因见 §8。

### 更值得注意的是排序变化

两次运行的召回集合只有 3/5 重叠：

| rank | CPU 运行 | CUDA 运行 |
| ---: | --- | --- |
| 1 | 咖喱炒蟹 (0.032787) | 咖喱炒蟹 `201000001` (0.032787) |
| 2 | 微波葱姜黑鳕鱼 (0.016129) | **咖喱肥牛 `201002309` (0.029437)** |
| 3 | 姜葱捞鸡 (0.016129) | 洋葱 `201000004` (0.016129) |
| 4 | 洋葱 (0.015873) | 姜葱捞鸡 `201002434` (0.016129) |
| 5 | 卤菜 (0.015873) | 土豆 `201001901` (0.015873) |

Top-1 稳定，但第 2 名换人了。**咖喱肥牛的 0.029437 可以精确反解**：

```
0.029437 = 1/(60+6) + 1/(60+10)
         ↑ dual_level rank 6   ↑ bm25 rank 10
```

意思是它**在两个源都只是中游（第 6、第 10），但两源叠加后总分超过了只在单源拿第 2 的洋葱**（0.016129）。

> 这是 RRF 的一个特性，也是它的风险：**多路一致的弱相关 > 单路强相关**。
> 对当前这个「找具体某道菜」的问题，咖喱肥牛明显是噪声（用户问的是咖喱炒蟹），
> 却因为"两路都沾边"被抬到第 2。
>
> 这一点对后续实验有直接影响：
> **相似度基线（如 A5 消融）和 RRF 的失败模式不同**，
> 前者是"单路高分误判"，后者是"多路弱一致误判"。分析错误案例时要分开归因。

## 7. 链路的每一环

```
Neo4j（5803 节点 / 20248 关系）
  └─ GraphDataPreparationModule: 322 菜谱 → 文档 → 分块（322 文档，约 3000 chunk）
       └─ BGE-small-zh（512 维，device 可配）→ Milvus Lite（FLAT 索引）
            └─ HybridRetrieval: dual_level + bm25 → RRF 融合 → top-5
                 └─ GenerationIntegration: 拼上下文 → 魔搭 API → 回答
```

**已通**：Neo4j → 分块 → embedding → 向量索引 → 混合检索 → RRF → 生成。

## 8. 性能诊断

### 8.1 三次运行的总量对比

| 运行 | 设备 | 检索 | 生成 | 端到端 |
| --- | --- | ---: | ---: | ---: |
| 1 | cpu | 14 815 ms | 17 775 ms | 32 590 ms |
| 2 | cuda | 12 685 ms | 24 859 ms | 37 544 ms |
| 3 | cuda + 计时打点 | 16 760 ms | 23 432 ms | 40 192 ms |

> 生成耗时不能直接横向比：三次回答长度不同，输出越长解码越久。
> **要比生成速度，得用「ms / 输出字符数」或固定 max_tokens。**
> 检索耗时的波动（12.7–16.8 s）也说明**云端 LLM 的响应时间不稳定**。

### 8.2 分段计时：真凶在 dual_level

第三次运行加了打点（`perf/timing` 分支），这是唯一可信的归因依据：

| 阶段 | 耗时 | 占比 |
| --- | ---: | ---: |
| **dual_level** | **14 808.8 ms** | **88.4%** |
| vector | 1 949.5 ms | 11.6% |
| bm25 | 2.0 ms | 0.0% |
| rrf | 0.1 ms | 0.0% |
| parent_doc | 0.0 ms | 0.0% |
| other | 0.0 ms | 0.0% |
| **total** | **16 760.4 ms** | |

**结论：88.4% 的时间花在 `dual_level` 上，与 embedding 设备、与 FLAT 索引都无关。**

第三次运行的召回（供对照）：

| rank | 菜谱 | node_id | RRF 分 | 来源 |
| ---: | --- | --- | ---: | --- |
| 1 | 咖喱炒蟹 | 201000001 | 0.032787 | dual_level |
| 2 | 青蟹 | 201000002 | 0.016129 | dual_level |
| 3 | 姜葱捞鸡 | 201002434 | 0.016129 | bm25 |
| 4 | 洋葱 | 201000004 | 0.015873 | dual_level |
| 5 | 卤菜 | 201002224 | 0.015873 | bm25 |

**这张表值得单独看**：第 2 名 `青蟹` 和第 4 名 `洋葱` 都是 **Ingredient 节点**，不是菜谱。
查 CSV 确认：`201000002` = Ingredient/青蟹，`201000004` = Ingredient/洋葱。

这不是偶发——**三次运行全部混入了食材节点**：

| 运行 | Recipe | Ingredient |
| --- | ---: | ---: |
| 1 | 4 | 1 |
| 2 | 3 | 2 |
| 3 | 3 | 2 |

> **影响（对后续实验很关键）**：
> 1. `num_docs=5` 不代表「5 道菜」，其中混着食材。做 Recall@k 时如果把食材当菜谱算，
>    命中率会被**系统性高估**。
> 2. 判定器的「实体覆盖」必须能区分节点类型，否则「食材命中」会被误算成「菜谱命中」，
>    让 SUFFICIENT 判定过于乐观。
> 3. Day 3 图重建后要确认：这是检索层的问题（应该按 node_type 过滤），
>    还是索引本身就把食材和菜谱混在一个 collection 里。

### 8.3 根因：检索路径里藏着一次 LLM 调用

`dual_level_retrieval()` 的第一步是 `extract_query_keywords()`，
后者**每次检索都发一次 LLM 请求**（`hybrid_retrieval.py:203`）：

```python
response = self.llm_client.chat.completions.create(
    model=self.config.llm_model,
    messages=[{"role": "user", "content": prompt}],   # 用 LLM 抽实体/主题关键词
    ...
)
```

整条 `hybrid` 检索链里**只有这一处 LLM 调用**（已用 AST 扫描确认）。
一条 14.8 秒的检索，大部分时间在**等云端 LLM 返回关键词**。

> 这也解释了为什么开 CUDA 只快 14%——GPU 加速的是 embedding，
> 而 embedding 在总耗时里只占很小一部分。

### 8.4 我猜错了两次

| 猜测 | 依据 | 结果 |
| --- | --- | --- |
| ① BGE 跑在 CPU | `device='cpu'` 写死，T4 闲置 | 开 CUDA 只快 14.4%，**证伪** |
| ② FLAT 暴力检索 | 3000×512 维在 CPU 上算 | vector 只占 11.6%，**证伪** |
| ③ **LLM 抽关键词** | 分段打点后才发现 | **确认，占 88.4%** |

> **教训：性能问题必须分段测量，凭经验猜会连错两次。**
> 两次猜测都「看起来很有道理」，但都没打点验证过。

### 8.5 优化方向

按收益排序：

| 方案 | 预期收益 | 代价 | 风险 |
| --- | --- | --- | --- |
| 缓存关键词提取结果 | 同 query 只调一次 | 小 | 低 |
| **规则化抽取**（Aho-Corasick 词典匹配） | 省掉 LLM，降到毫秒级 | 中 | 准确率需验证 |
| 换更快的 LLM / 本地小模型 | 取决于模型 | 中 | 可复现性下降 |
| 并行 dual 与 vector | 省 11.6% | 小 | 需改并发 |

> **推荐方向**：Day 4 本来就要做 **Aho-Corasick 实体链接器**。
> 它正好能替代这里的 LLM 关键词提取——**零 LLM 成本、可复现、毫秒级**。
> 两件事合并成一件事，不用额外排期。

> **另一层意义**：这个发现证明「用 LLM 做检索前置步骤」会带来隐藏延迟。
> 项目主张是「判定器零 LLM 调用」，而现在**连检索本身都在偷偷调 LLM**——
> 如果不打点，这个成本会被算到「检索」头上，让 Ours 的成本优势被低估。

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

A：**先分段、再归因**，中间猜错了两次。

第一次猜「BGE 跑在 CPU」——开了 CUDA 后只快 14.4%，证伪。
第二次猜「FLAT 暴力检索」——加计时打点后，实测数据推翻：

```
dual_level_ms  14808.8  (88.4%)   ← 真凶
vector_ms       1949.5  (11.6%)
bm25_ms            2.0   (0.0%)
rrf_ms             0.1   (0.0%)
total_ms       16760.4
```

**88.4% 在 `dual_level`，跟 embedding 设备、跟 FLAT 索引都无关。**

再往里看 `dual_level_retrieval`，它第一步调 `extract_query_keywords()`，
而这个函数**每次检索都发起一次 LLM 请求**（`hybrid_retrieval.py:203`）：

```python
response = self.llm_client.chat.completions.create(...)   # 用 LLM 抽关键词
```

一条 14.8 秒的检索里，**大部分时间在等云端 LLM 返回关键词**，
不是在算向量，也不是在查库。

> 教训：**性能问题必须分段测量，凭经验猜会连错两次。**
> 我第一次猜 CPU 编码（错），第二次猜 FLAT 索引（也错），
> 真正原因是藏在调用链深处的 LLM 依赖——只有打点才能看到。

**Q4：`.env` 里的密钥怎么保证不泄露？**

A：三道防线：

1. `.gitignore` 第一行就是 `.env`，`git check-ignore` 可验证
2. 仓库里只有 `.env.example`，值是占位符
3. `to_dict(mask_secrets=True)` 在配置落盘时把密码替换为 `***`，
   所以 `results/config_snapshot.json` 可以直接入库

> 反例：`milvus_test.db` 曾被误提交进仓库（Day 1 之后），因为 `.gitignore`
> 只写了 `milvus_data.db` 没覆盖测试文件。**忽略规则要用通配符，不要逐文件列举。**
