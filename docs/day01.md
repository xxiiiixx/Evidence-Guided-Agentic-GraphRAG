# Day 1 · 2026-10-20 · 环境验证 + 图数据库落地

> 状态：**通过**。Neo4j 起来，数据导入完整，零丢失。
> 原始输出：`results/day01_env_check.txt`、`results/day01_db_check.txt`、`results/neo4j_stats.json`

## 1. 环境快照

| 项 | 值 | 影响 |
| --- | --- | --- |
| GPU | **Tesla T4** 15360 MiB，compute cap **7.5** | 不支持 BF16，**7B FP16 必 OOM** |
| CPU / 内存 / 磁盘 | Xeon 8255C 8 核 / 30 GiB / 245 GB 可用 | 够跑 Neo4j + Milvus |
| 系统 | Ubuntu 24.04.3，kernel 6.6.92 | — |
| Python | 3.10.22（conda env `graphrag`） | 符合计划要求 |
| Docker / Compose | 27.5.1 / v2.33.0 | — |
| Java | OpenJDK 20 | tarball 方案需 17/21，20 待确认 |
| 网络 | HF 200、ModelScope 302、PyPI 200 | 可直连 HuggingFace，无需离线权重 |

**模型选型结论（Day 2 依据）**：T4 显存 15 GB 且 compute 7.5，7B FP16 不可行。
**Day 2 先用 Qwen2.5-3B 4bit 打通链路**，再试 7B-4bit；1.5B 仅作兜底。

## 2. 数据库结果

`neo4j-init` exit=0，`neo4j-db` 首次轮询即 healthy。数据库与 CSV 的差异**全部有解释**：

| 类别 | 数量 | 来源 |
| --- | ---: | --- |
| CSV 原始节点 | 5760 | `nodes.csv` |
| + Root 重复导入 | +1 | 导入脚本两个 LOAD CSV 分支都命中 |
| + 脚本自动派生 | +42 | Category 19 / ConceptType 8 / GraphStatistics 8 / RelationshipStatistics 7 |
| **= 节点总数** | **5803** | |
| CSV 原始关系 | 6055 | `relationships.csv` |
| + 脚本自动派生 | +14193 | HAS_CONCEPT_TYPE / BELONGS_TO_CATEGORY / NEXT_STEP / SIMILAR / USES_SAME_TOOL / USES_SAME_METHOD |
| **= 关系总数** | **20248** | |

核心业务关系与 CSV 完全一致：`REQUIRES 2905`、`CONTAINS_STEP 2514`、`BELONGS_TO 314`、`DIFFICULTY_LEVEL 322`。

## 3. 关键基线（Day 3 的判据）

| 指标 | 实测 | 含义 |
| --- | ---: | --- |
| 每个食材节点所属菜谱数 | **恒为 1**（2905 个） | 图 = 322 个互不相交的星 |
| 被 ≥2 道菜共享的食材节点 | **0** | **连通性判定退化为查表** |
| 不同食材名 | **759**（含元节点）/ 758 | Day 3 合并目标 |
| 合并后跨 ≥2 道菜的名字 | **268** | 合并后才有真实多跳 |
| 含 ≥2 种食材的菜谱 | 320 / 322 | 两跳候选池 |
| REQUIRES 有 amount | 2896 / 2905 | 99.7%，数据质量良好 |

> **0 → 268 是 Day 3 的验收线。** 达不到说明合并逻辑错。

## 4. 发现并修复的两个 bug

**① 对比表全 0（真 bug）**
`cypher-shell --format plain` 给字符串列加双引号（`"Ingredient", 2906`），`parse()` 未剥离，
键变成 `"Ingredient"` 与预期表 `Ingredient` 不匹配，导致第 6 节 12 项全部误报 `<-- 检查`。
修复：解析时 `strip('"')` 并跳过表头。

**② 预期值不全（我的问题）**
初版只算 CSV 数据，漏了脚本派生的 42 节点 / 14193 关系，`TOTAL` 必然失败。
已补全并标注每项来源为「CSV」或「脚本派生」。

修复后重跑真实日志：**全部一致，0 项不符**。

## 5. 产物

| 文件 | 内容 |
| --- | --- |
| `results/neo4j_stats.json` | 数据库计数 + 预期值 + 差异说明 |
| `results/day01_env_check.txt` | 环境快照原始输出 |
| `results/day01_db_check.txt` | 数据库自检原始输出 |
| `docs/day01.md` | 本文件 |
| `scripts/day01_db_check.sh` | 修复 parse bug + 补全预期值 |

---

## 6. 面试问答

**Q1：`neo4j-init` 跑完就退出，你怎么知道导入了？静默失败怎么发现？**

A：容器退出码是唯一信号——`docker inspect --format='{{.State.ExitCode}}' neo4j-init`，
非 0 就是失败，再看 `docker logs neo4j-init` 定位。`restart: "no"` 保证它只跑一次。

**但退出码 0 不等于数据对**：`cypher-shell -f` 遇到某些错误不会中断脚本。
所以要**二次校验**——用 `MATCH (n) RETURN labels(n)[0], count(*)` 拿真实计数，
与 CSV 推算值逐项对比，再加 `TOTAL` 行兜底（防止某个 label 整体缺失却不被单列发现）。

> 今天就是靠这个抓到了 parse bug：数据都对，但对比表全 0。**没有 TOTAL 行会误判成导入失败。**

**Q2：5803 vs 5760，多出 43 个是什么？面试说哪个数？**

A：多出的是 **42 个脚本派生节点 + 1 个 Root 重复导入**。

- Category 19：按 `n.category` 逗号分割后 MERGE 出的分类节点
- ConceptType 8：按 `conceptType` 去重生成
- GraphStatistics 8 / RelationshipStatistics 7：脚本自己生成的统计快照（本质是元数据，不该算进业务图）

**面试回答 5760**（CSV 原始），并主动说明另外 43 个是导入脚本的派生结构。
说 5803 会让人误以为数据规模更大，被追问就露馅。

更进一步：**GraphStatistics 这类"统计的统计"节点应该在 Day 3 重建时去掉**——它们会污染
`MATCH (n)` 的全图遍历，也让 `SIMILAR`/`USES_SAME_TOOL` 这类 `LIMIT 1000` 的伪关系混进证据集。

**Q3：怎么发现「每个食材只属于 1 道菜」的？独立做项目第几天能想到？**

A：**先算基础统计，再看分布，而不是先写代码。**

具体路径：CSV 统计时发现 `REQUIRES = 2905` 恰好等于 `食材数 2905`，
这个"巧合"可疑 → 查 `每个食材被多少道菜引用`，得到分布 `{1: 2905}` → 确认是完美划分。

**关键判断**：连通性判定在完美划分图上等价于"是否同菜"，这是查表不是推理，
E1 的 AUC 必然趋近 1.0，实验就没有区分度。

**诚实的回答**：独立做的话我可能第 10 天跑 E1 时才发现（AUC 异常高），
然后倒推回去查数据。**这次是提前查数据避开了**——教训是：
**方法依赖某个数据特征时，应该在写代码前先验证该特征存在**，而不是等实验结果异常再回头。

> 这也是为什么计划里 Day 3 被标成分水岭：它是方法成立的前提，不是优化项。
