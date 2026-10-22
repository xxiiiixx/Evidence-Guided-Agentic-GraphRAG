# 证据可验证的自适应检索智能体 — 28 天计划

> Day 1 = **2026-10-20** · Day 13 = **11/01（简历 v1 + 开始投递）** · Day 27 = **11/15（截止）**
> 每天 6 h。**每天必须落一个可验证文件**。验收不过，次日先补，不加新功能。
> 目标：11 月中旬前拿到可投递的实习简历，边投边做。

## 里程碑

| 节点 | 日期 | 必须达到 |
| --- | --- | --- |
| M1 | Day 7 · 10/26 | 图重建完成，Benchmark 40 条 dev 可回查 |
| M2 | Day 12 · 10/31 | **Ours 在 dev 上有真实数字**（能写进简历） |
| M3 | Day 13 · 11/01 | **简历 v1 定稿，开始投递** |
| M4 | Day 20 · 11/08 | E1 / B5 / A1 齐活，简历更新 v2 |
| M5 | Day 27 · 11/15 | 面试准备完成，投递复盘 |

---

## 阶段一：主干跑通（D1–12，10/20–10/31）

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **1** | 10/20 | 服务器：`nvidia-smi`、`docker --version`、`python3 -V`、conda 建 3.10 env；拉仓库；`docker compose -f data/C9/docker-compose.yml up -d`；确认 `neo4j-init` 退出码 0；跑节点/关系计数 | `results/neo4j_stats.json`、`docs/day01.md` | 计数与 CSV 统计一致或差异有解释 |
| **2** | 10/21 | ① `config.py` 改环境变量 + `to_dict()` 落盘 ② `main.py` 加 `--question/--batch/--mode` ③ 向量库改用 **Milvus Lite**（standalone 在服务器 panic，且 3000 chunk 无需独立服务）④ LLM 走魔搭 API（本地模型推迟到 Day 7 前）⑤ 跑通 1 条端到端 | `results/smoke_trace.jsonl`、`results/config_snapshot.json`、`docs/day02.md` | `--question` 单条可跑；`hybrid` 模式 Top-1 命中目标菜谱 |
| **3** | 10/22 | **★ 图重建**（七件事，Day 1 普查后细化）：① 按 `name` 合并食材 2905 → 758，再按同义词归一 → 746（255 个跨菜谱）② **同义词归一**：大蒜/蒜、食用油/油、白糖/糖、姜/生姜、葱/小葱、酱油/生抽 等，`synonyms` 字段不含这些，需手工规则表 ③ **剔除派生节点与伪关系**：GraphStatistics/RelationshipStatistics 等 42 节点、SIMILAR/USES_SAME_TOOL/USES_SAME_METHOD 等 14193 条 `LIMIT 1000` 截断边 ④ **剔除器具误抽**（刷子/保鲜膜/蘸料碟）并补齐缺失的 4 类 RecipeCategory 节点（半成品/凉菜/下酒菜/减肥餐，涉及 13 道菜）⑤ **去重**：陈皮排骨汤（201003873/201004002）食材与步骤完全相同，删一条，322 → 321 道 ⑥ **拆分 `category` 语义**：Recipe 与 Ingredient 共用该字段但枚举不兼容（13 种 vs 7 种），改为 `recipeCategory` / `ingredientCategory` 独立属性；重写导入 Cypher 后重导 ⑦ **收尾**：删掉因去重而失去父节点的 `CookingStep`；`relationshipId` 按输出顺序重编号（原 6055 条边只有 257 个唯一值，无法当主键） | `data/rebuild/`（nodes.csv / relationships.csv / neo4j_import.cypher / docker-compose.yml）+ `results/graph_rebuild.json` | `REQUIRES` 变多对多；**被 ≥2 道菜共享的食材从 0 → 255（34.2%）**（纯 name 合并理论值 268，同义词归一后 259，再去掉重复菜谱的 7 条边后 255）；食材名 759 → 746；派生节点/伪关系数为 0；13 道菜的 category 不再丢；菜谱数 322 → 321；无跨 label 的 `category` 混用；**确认 chunk 只挂在 Recipe 节点上**（Day 2 三次召回均混入 1–2 个 Ingredient，见 `docs/day02.md` §8.2） |
| **4** | 10/23 | 实体链接器：Aho-Corasick 匹配 758 食材 + 321 菜谱 + `synonyms`；**处理 11 个跨 label 同名的类型消歧**（小龙虾/牛排/凉皮 等既是 Recipe 又是 Ingredient，需按问句模式判定）；**同时用它替代 `dual_level_retrieval` 里的 `extract_query_keywords()` LLM 调用**（Day 2 实测该调用占检索 88.4%）；20 条中文 query 人工核验 | `rag_modules/entity_linker.py` + 单测 | 准确率 ≥ 85%；同名跨类型样本能正确归到 Recipe 或 Ingredient；检索不再混入 Ingredient 节点 |
| **5** | 10/24 | 修 `_find_entity_relations`：一跳真实 Cypher，保留 nodeId / relId / 方向 | 代码 + `results/path_samples.json` | 能返回真实路径并贴出 nodeId |
| **6** | 10/25 | 修 `_find_shortest_paths`：`shortestPath` + 深度/条数上限 | 代码 + `tests/test_graph_paths.py` | 两跳可查，无路径爆炸 |
| **7** | 10/26 | Benchmark 生成器：双来源过滤 + 路径等价规则 **→ M1** | `evaluation/benchmark_builder.py`、`dev.jsonl` | 40 条 dev，Gold 可回查率 ≥ 80% |
| **8** | 10/27 | 扩到 200 条，按实体分组划 dev/test 防泄漏；人工抽检 20 条 | `data/benchmark/dev.jsonl`、`test.jsonl` | 五类 bucket 齐全（含无答案、贪心失效） |
| **9** | 10/28 | B1（仅 Hybrid）/ B2（仅 Graph）跑 dev 40 条 | `results/b1.jsonl`、`b2.jsonl` | 每条有 trace，可复算 |
| **10** | 10/29 | B3（共享修复的 One-shot Router）跑 dev | `results/b3.jsonl` | 与 B1/B2 同一集合，指标可对齐 |
| **11** | 10/30 | `rag_modules/evidence.py` 统一 Evidence + 跨源去重；`sufficiency.py` 的 `assess()` | 代码 + `tests/test_sufficiency.py` | 正例判充分、"两实体无关联"反例判不足 |
| **12** | 10/31 | `--mode adaptive` 接上，Ours 跑 dev 40 条 **→ M2** | `results/adaptive_dev.jsonl` | 每条有 state→action 轨迹；**Ours vs B3 有第一组对比数字** |

---

## 阶段二：判定器与对照（D13–20，11/01–11/08）

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **13** | 11/01 | **简历 v1 定稿 + 开始投递**（项目描述只用 Day 12 前的真实数字）**→ M3** | `docs/resume_v1.md` | 能投；三条 bullet 都有数字支撑 |
| **14** | 11/02 | **E1：判定器 AUC**（连通性 vs 仅实体覆盖 vs top-1 相似度） | `results/e1_auc.json` | **0.70 < AUC < 0.95 才算健康**（≥0.95 说明图仍太简单，回 Day 3） |
| **15** | 11/03 | **B5：LLM 自评"证据够不够"** 跑 dev | `results/b5.jsonl` | 有 LLM 判定记录；与 Ours 同集合对比 |
| **16** | 11/04 | Oracle 动作序列表（≤2 步穷举）+ E2 Agent 决策指标 | `results/oracle.json`、`e2_agent.json` | 决策准确率 / 过度检索率 / 过早停止率可算 |
| **17** | 11/05 | **A1 消融**：判据退化为"仅实体覆盖"（去连通性） | `results/ablation_a1.json` | A1 相对 Ours 有可解释的掉点 |
| **18** | 11/06 | `evidence_controller.py` 收敛：预算控制 + 防死循环 + 失败恢复 | 代码 + `tests/test_controller.py` | Mock 可测；不超预算 |
| **19** | 11/07 | 冻结 test，Ours / B3 / B5 跑 160 条 | `results/ours_test.jsonl` 等 | test 冻结后未改动 |
| **20** | 11/08 | Bootstrap 95% CI + 成本分析 + 错误分类 **→ M4** | `results/final_metrics.json`、`cost.json` | Ours vs B3 vs B5 有真实数字 + CI；≥3 个失败案例能解释 |

---

## 阶段三：面试准备与投递（D21–27，11/09–11/15）

| Day | 日期 | 任务 | 产物 | 验收 |
| --- | --- | --- | --- | --- |
| **21** | 11/09 | README 重写（问题→方法→实验→限制）+ 架构图 | `README.md` | 30 秒能看懂价值；数字与 `results/` 一致 |
| **22** | 11/10 | limitations 文档 + 负结果如实写 | `docs/limitations.md` | 每个失败案例有归因；没赢就写没赢 |
| **23** | 11/11 | 八股①：RAG / GraphRAG / Agent 原理（RRF、判定 vs 打分、Self-RAG/CRAG 区别、贪心失效） | `docs/interview_rag.md` | 能扛 2 层追问 |
| **24** | 11/12 | 八股②：Transformer / PyTorch / 向量检索 / 手撕 attention | `docs/interview_dl.md` | 手写 attention 无错 |
| **25** | 11/13 | 算法题：手撕 + 常考题型刷一轮 | 刷题记录 | 中等难度 30 分钟内出解 |
| **26** | 11/14 | 模拟面试 + 2 / 5 分钟项目讲述稿 | `docs/pitch.md` | 对着代码讲 10 分钟不卡 |
| **27** | 11/15 | 投递复盘 + 补投 + 简历 v3（按面试反馈修） **→ M5** | 投递记录 | 已投岗位 ≥ 目标数 |

---

## 附：Day 28（11/16）及以后

不排任务。若前面有缺口在这天补；否则继续投递与面试。

---

## 本计划的取舍（面试可能被问到，也是自我提醒）

**做了**：B1 / B2 / B3 / B5 / Ours、E1 判定器 AUC、A1 消融、E2 Agent 决策指标、图重建。

**砍了**：B4（Always-Combined）、A2–A6 其余消融、E4 图扰动、E5 模型交叉验证。理由：11/15 截止，优先保证有真实数字能投递；这些属于加分项，不是必需项。若时间富余，按 A2 → A3 → E4 的顺序补。

**图重建是本计划的必要前提**：原始 C9 数据里每个食材节点只属于 1 道菜（2905 关系 / 2905 食材 / 0 个跨菜谱食材），连通性判定会退化为"查表"，E1 的 AUC 必然接近 1.0 而没有区分度。按 `name` 合并后 758 个真实食材中有 268 个跨多道菜，同一食材对最多对应 62 道菜，多跳才成立。
