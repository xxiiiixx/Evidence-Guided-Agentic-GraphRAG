# Day 4 执行记录：用图谱词表替换 LLM 抽关键词

日期：2026-10-23

## 0 一句话结论

把 `dual_level_retrieval` 里那次耗时 14.8 s 的 LLM 调用，换成在 1 280 个图谱词表项上的 Aho-Corasick 匹配：单条问句 **0.008 ms**，21 条标注问句 **21/21**，词表构建一次性 **32.2 ms**。

顺带修掉 5 处指向旧 schema 的代码——它们不抛异常，只是静默返回空结果，比报错更难发现。

## 1 为什么换

### 1.1 Day 2 的分段计时

`perf/timing` 分支上实测一条问句（`docs/day02.md` §8.3）：

| 阶段 | 耗时 | 占检索 |
| --- | --- | --- |
| `dual_level_retrieval` | 14 808.8 ms | **88.4%** |
| 其中 `extract_query_keywords()`（一次 LLM 调用） | 14 799 ms | 88.3% |
| 向量检索 | 1 949.5 ms | 11.6% |
| BM25 | 2.0 ms | 0.01% |
| RRF 融合 | 0.1 ms | <0.01% |
| parent-doc 回填 | 0 ms | — |
| **检索合计** | **16 760.4 ms** | 100% |

那次调用的产出是「实体关键词 + 主题关键词」两个列表。也就是说：**检索的第一步花 14.8 秒，只为得到一个字符串列表**。

### 1.2 它在猜名字

旧提示词让模型「推测相关的具体食材/菜品」，例子是「鸡胸肉」「西兰花」。模型不知道图谱里有什么，而下游 `graph_indexing.get_entities_by_key()` 是**按名字精确查字典**：

- 模型猜的名字不在图上 → 整条落空，白白花 14.8 秒；
- 模型猜的名字撞上别的节点 → 召回错误的内容。

图谱里一共只有 1 304 个候选（321 菜谱 + 746 食材 + 别名 + 主题词）。这不是需要推理的任务，是查表任务。

### 1.3 Day 2 召回混进 Ingredient 的直接原因

Day 2 三次运行的召回里，每次都有 1–2 条是 Ingredient 节点（`201000002` 青蟹、`201000004` 洋葱），而 chunk 只挂在 Recipe 上（Day 3 已确认 `build_recipe_documents()` 只 `MATCH (r:Recipe)`）。

原因在两层叠加：

1. `entity_kv_store` 以 nodeId 为键，但 `get_entities_by_key(name)` 是按名字建的索引，**不区分类型**；
2. LLM 猜出来的「青蟹」「洋葱」正好是食材名 → 直接命中食材节点，作为一条 retrieval 结果进了 top-5。

换成语表匹配后，模型不再有机会凭空造出食材名；而用户真的问「洋葱怎么挑」时仍会命中 Ingredient——那是正确行为，和 Day 2 的「混入」不是一回事。

## 2 做了什么

| # | 动作 | 位置 |
| --- | --- | --- |
| ① | 新增实体链接模块：Aho-Corasick（failure link + 输出链，纯 Python）+ 最左最长不重叠匹配 | `rag_modules/entity_linker.py`（582 行） |
| ② | 词表 = Recipe 名/别名 + Ingredient 名/别名 + `mergedAliases` + recipeCategory + cuisineType + 难度档位 | `EntityLinker.build()` |
| ③ | 11 组跨 label 同名的类型消歧：倾向词判定，判不出按 Recipe 并标 `ambiguous` | `EntityLinker._resolve()` |
| ④ | 接入 `dual_level_retrieval`，用 `config.keyword_source`（`linker` / `llm`）切换，保留旧路径给 Day 7 消融 | `hybrid_retrieval.py:494-518` |
| ⑤ | 实体级检索改用 `Match.node_id` 直取 `entity_kv_store`，不再按名字查字典 | `hybrid_retrieval.py:231-277` |
| ⑥ | 修 5 处旧 schema 引用（见 §2.1） | 4 个文件 |
| ⑦ | 32 条单测 + 21 条标注问句评测脚本 | `tests/test_entity_linker.py`、`scripts/eval_entity_linker.py` |

### 2.1 五处静默失效的旧 schema 引用

这些地方都不会报错——Cypher 的 `OPTIONAL MATCH` 匹配不到只是给 null，Python 的 `elif` 分支进不去只是少挂几个键——所以只能靠读代码发现。

| 位置 | 旧写法 | 重建图里实际是 |
| --- | --- | --- |
| `graph_data_preparation.py:100` | `(r)-[:BELONGS_TO_CATEGORY]->(c:Category)` | `(r)-[:BELONGS_TO]->(c:RecipeCategory)` |
| `graph_data_preparation.py:107,110` | `COALESCE(r.category, ...)` | `r.recipeCategory` |
| `graph_indexing.py:239` | `relation_type == "HAS_STEP"` | `CONTAINS_STEP` |
| `graph_indexing.py:247` | `relation_type == "BELONGS_TO_CATEGORY"` | `BELONGS_TO` |
| `hybrid_retrieval.py:435,444` | `r.category CONTAINS ...` / `r.category as category` | `r.recipeCategory` |

第一处的后果最严重：分类全部变成「未知」，而因为在 `OPTIONAL MATCH` 里，日志上一切正常。

### 2.2 另外两处必须一起改的逻辑

**按名字去重会把实体删掉。** `graph_indexing.deduplicate_entities_and_relations()` 原来按 `entity_name` 分组，把同名实体的内容并进第一个、并**从 `entity_kv_store` 删除**其余的。11 组菜谱/食材同名（牛排、小龙虾、凉皮…）里，食材那一半会被合并掉。改成按 `(entity_name, entity_type)` 去重后两者共存；这也是⑤能按 nodeId 取到实体的前提。

**同义词归一必须读 `mergedAliases`。** Day 3 把「生姜」并进「姜」，原名只写进了 `mergedAliases` 列（纯文本、逗号分隔），而 `synonyms` 是 JSON 数组。只读 `synonyms` 的话，「生姜」查不到「姜」——Day 3 那 9 组归一在检索侧等于没做。这个坑是评测脚本报出来的：`大蒜和生姜` 只命中了大蒜。

## 3 词表构成（实测）

| 指标 | 值 | 说明 |
| --- | --- | --- |
| `recipes` | 321 | 参与建表的菜谱节点数 |
| `ingredients` | 746 | 同义词归一之后 |
| `surfaces` | 1 280 | 不同的表面形式（= 匹配模式数） |
| `entries` | 1 299 | surface→实体 的对应条数 |
| `recipeEntries` | 422 | 多出 101 条是菜谱别名 |
| `ingredientEntries` | 777 | 多出 31 条是 synonyms + mergedAliases |
| `topicEntries` | 100 | 13 分类 + 31 菜系 + 别名 + 5 档难度 |
| `skippedShortSurface` | 14 | 单字表面形式，按设计丢掉（见 §6.1） |
| `collisions` | 19 | 同一 surface 对应多个 entry（拆解见 §4） |

`collisions = 19` 的构成：11 组跨 Recipe/Ingredient 同名 + 7 组「别名撞正名」+ 1 组主题词撞实体（面食/主食）。

## 4 消歧策略

### 4.1 跨 label 同名（11 组）

| 表面形式 | Recipe nodeId | Ingredient nodeId |
| --- | --- | --- |
| 凉皮 | 201003534 | 201003535 |
| 凉粉 | 201004407 | 201004604 |
| 小龙虾 | 201000160 | 201000161 |
| 手抓饼 | 201000571 | 201000757 |
| 油泼辣子 | 201000838 | 201002476 |
| 油酥 | 201000768 | 201004641 |
| 牛排 | 201002876 | 201002877 |
| 蔗糖糖浆 | 201000877 | 201001347 |
| 速冻水饺 | 201003618 | 201000614 |
| 速冻汤圆 | 201003676 | 201003677 |
| 速冻馄饨 | 201003631 | 201003632 |

判定顺序：问句窗口里出现菜谱倾向词（怎么做/做法/步骤/教程/菜谱/烹饪）→ Recipe；出现食材倾向词（买/用了/需要/配料/食材/替代/换成/多少）→ Ingredient；都没有 → 按 Recipe 解释并标 `ambiguous=True`，把「这里其实是猜的」写进 metadata 而不是藏着。

### 4.2 别名撞正名（7 组）

真实数据里存在「食材甲的 synonyms 里有食材乙的正名」：

`大料`↔`八角`、`茴香`↔`八角`、`马铃薯`↔`土豆`、`甜椒`↔`青椒`、`番茄`↔`西红柿`、`茄子`↔`青茄子`、`香葱`↔`葱`

这类冲突里**正名优先于别名**（`_SOURCE_RANK`）。按入表先后决定是错的——`八角` 比 `大料` 先入表时，问「大料」会被解释成八角。

## 5 实测

| 项 | 结果 |
| --- | --- |
| 单测 | 32 条全通过（`python -m unittest discover -s tests`） |
| 标注评测 | **21/21 = 100%**，总耗时 0.170 ms，最慢单条 0.025 ms |
| 词表构建 | 32.2 ms（进程内一次） |
| 单词链接 | 0.0080 ms（1 000 次平均） |
| 对照：旧 LLM 抽词 | 14 799 ms |

评测集覆盖：裸菜名、菜名+问法、同名裸词、两名同现、同义词归一（生姜→姜、生粉→淀粉）、多食材、别名近义词对（生抽/老抽）、单字名取舍（料酒和盐）、菜系与分类主题词。

评测脚本把「标注里写了词表里没有的名字」单列为 `annotationErrors`，避免标注写错被当成链接失效——实际跑出来一次，就是上面 §2.2 的 `mergedAliases` 问题。

## 6 已知缺陷与取舍

### 6.1 单字实体名不进词表（11 个：冰 姜 水 盐 米 肉 葱 虾 醋 面 鸡）

「水煮鱼」里的水、「盐水鸭」里的盐、「红烧肉」里的肉都会被误命中，信息量却接近零。代价是「盐放多少」这类问句一个实体都匹配不到，检索退回向量 + BM25（评测里 `料酒和盐哪个先放` 只命中料酒，是刻意保留的样本）。

主题词不受这条限制（「汤」有用，且总被更长的菜名压住）。

### 6.2 只认图上有的名字

「松花蛋」→「皮蛋」、「土豆」→「马铃薯」这类图上没有的等价表达，词表覆盖不到。词表方案的准确率上限就是**词表覆盖率**，不会像 LLM 那样偶尔蒙对没见过的说法。

### 6.3 不处理错别字与语序

「咖哩炒蟹」（哩/喱）匹配不到。Aho-Corasick 是精确子串匹配，容错为零；要容错得上编辑距离或向量召回，那是 Day 5 之后的事。

### 6.4 词表构建依赖 `data_module` 的加载顺序

`EntityLinker.build()` 在 `_build_graph_index()` 里、`deduplicate_entities_and_relations()` 之后调用，词表与 KV 索引同源。若以后改成从 CSV 建表（`from_nodes_csv()` 已支持），两边可能漂移，需要在测试里比对节点数。

## 7 未做

- **没在服务器上端到端实跑**。本机没有 neo4j / pymilvus / openai SDK，验证只到「静态编译 + 单测 + 基于 Day 3 产物的离线评测」。端到端要看的是「召回里不再出现 Ingredient」和「检索耗时里 LLM 那段消失」，命令：

```bash
cd data/rebuild && docker-compose up -d
export NEO4J_URI=bolt://localhost:7688 MILVUS_LITE_PATH=./milvus_rag.db
python main.py -q "咖喱炒蟹怎么做？" -m hybrid --trace
```

- `keyword_source="llm"` 的旧路径保留了但没有回归测试（Day 7 做消融时一起补）。

## 8 面试问答

**Q1：为什么不用 jieba 分词 + 字典查？**
jieba 的分词边界和实体边界不一致：「咖喱炒蟹」会被切成「咖喱/炒/蟹」，词表里没有「咖喱」这个实体，反而可能命中「蟹」。Aho-Corasick 是「拿词表去扫文本」，词表里的词一定完整命中，不受分词粒度影响——这正是实体链接要的语义。

**Q2：为什么不用 AC 自动机的 C 扩展（pyahocorasick）？**
1 280 个模式在 30 字文本上，纯 Python 是 0.008 ms。省下的那点时间抵不过服务器上装 wheel/编译扩展的成本，也抵不过多一个依赖的维护成本。等词表上到十万级再说。

**Q3：0.008 ms 和 14.8 s 这个对比是不是有点取巧？**
是，但取巧的方向对我不利：LLM 那次调用还要付 token 钱、受 API 抖动影响、模型换代后行为会变。链接器是确定性的，同一条问句永远给同一个答案——这对 Day 7 的 benchmark 复现是硬要求。

**Q4：`ambiguous=True` 的样本怎么处理？**
只标记，不二次确认。metadata 里留下 `surface/name/label/source/ambiguous/reason`，Day 7 统计「歧义命中率」时直接按这个字段分组。不在这里加一轮 LLM 复核，是因为那等于把刚删掉的 14.8 s 又请回来。

**Q5：词表覆盖不到的说法怎么办？**
退回向量 + BM25——hybrid 本来就是三路融合，实体级只是其中一路。这里的原则是**宁可少命中，不可错命中**：错命中会把无关菜谱顶进 top-5，比召回不到更难排查。

**Q6：为什么把 `isMain`、`mergedAliases` 这些细节看得这么重？**
因为它们决定「这个数字是不是真的」。`mergedAliases` 漏读时，评测准确率是 20/21 而不是 21/21，差的正是「生姜→姜」这一条；如果不做标注集、只看「跑通了没有」，这个洞会一直留到 Day 7 才以「同义词归一没效果」的形式暴露。
