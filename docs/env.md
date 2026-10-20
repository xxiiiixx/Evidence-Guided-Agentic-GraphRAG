# 环境快照

> 采集于 2026-10-20 · commit `e9fc6a0` · 原始输出见 `results/day01_env_check.txt`
> 本文件是 Day 1 的产物，供后续实验复现与排障对照。

## 硬件

| 项 | 值 | 对项目的影响 |
| --- | --- | --- |
| GPU | **Tesla T4**，15360 MiB（约 15 GB），compute cap **7.5** | 见下节「模型选型结论」 |
| CPU | Intel Xeon Platinum 8255C @ 2.50GHz，8 核 | embedding 走 CPU 也可接受 |
| 内存 | 30 GiB（可用 24 GiB） | 够跑 Neo4j + Milvus |
| 磁盘 | 256 GB，已用 9.9 GB，可用 245 GB | 够放模型权重 |
| 系统 | Ubuntu 24.04.3 LTS，kernel 6.6.92 | — |

## 软件

| 项 | 版本 | 备注 |
| --- | --- | --- |
| Python | 3.10.22（conda env `graphrag`） | 符合计划要求（3.10/3.11，非 3.13） |
| conda | 23.5.2 | — |
| pip | 26.2.1 | — |
| Docker | 27.5.1 | — |
| Docker Compose | v2.33.0 | 支持 `docker compose`（v2 语法） |
| Java | OpenJDK 20（Zulu） | Neo4j tarball 方案需要 17/21，**20 需确认兼容性** |
| CUDA | 13.0（驱动 580.65.06） | — |

## 网络

| 目标 | 状态 |
| --- | --- |
| huggingface.co | 200 |
| modelscope.cn | 302（正常重定向） |
| pypi.org | 200 |

结论：**可直连 HuggingFace，模型权重无需预下载到离线介质。**

## 用户与权限

- 当前用户 `root`，**不在 `docker` 组**——因为是 root，无需 sudo 即可操作 Docker，但脚本里 `docker` 命令不需要加 sudo。
- 容器内环境（hostname 为容器 ID，repo 在 `/workspace`），据此外推：这是一个容器化开发环境，不是裸机。

## 模型选型结论（Day 2 依据）

T4 有两个硬约束，直接改变原计划的模型方案：

1. **15 GB 显存**，比计划假设的 16 GB 少约 1 GB。
2. **compute cap 7.5**，不支持 BF16（需 ≥8.0）。只能走 FP16 或 INT4/INT8 量化。

因此：

| 方案 | 可行性 | 说明 |
| --- | --- | --- |
| Qwen2.5-7B FP16 | ❌ | 权重约 15 GB，加 KV cache 必 OOM |
| Qwen2.5-7B AWQ/GPTQ 4bit | ✅ | 约 4.5 GB 权重，T4 的 INT4 走 compute 7.5 有支持 |
| **Qwen2.5-3B 4bit** | ✅ 推荐起步 | 更稳，留给 KV cache 与并发足够余量 |
| Qwen2.5-1.5B | ✅ 兜底 | 质量下降明显，仅用于打通链路 |

**Day 2 建议先用 3B 打通链路，再试 7B-4bit**，避免一上来卡在 OOM 上。

## 图数据现状（Day 1 实测，Day 3 的基线）

数据库实际计数与 CSV 的差异**全部有解释**，非导入失败：

| 类别 | 数量 | 来源 |
| --- | ---: | --- |
| CSV 原始节点 | 5760 | `nodes.csv` |
| + Root 重复导入 | +1 | 导入脚本两个分支都命中 |
| + 脚本派生节点 | +42 | Category 19 / ConceptType 8 / GraphStatistics 8 / RelationshipStatistics 7 |
| **= 数据库节点总数** | **5803** | |
| CSV 原始关系 | 6055 | `relationships.csv` |
| + 脚本派生关系 | +14193 | HAS_CONCEPT_TYPE / BELONGS_TO_CATEGORY / NEXT_STEP / SIMILAR / USES_SAME_TOOL / USES_SAME_METHOD |
| **= 数据库关系总数** | **20248** | |

### 关键基线（Day 3 图重建的对照）

| 指标 | 实测值 | 含义 |
| --- | ---: | --- |
| 每个食材节点所属菜谱数 | **恒为 1**（2905 个节点） | 图是 322 个互不相交的星 |
| 被 ≥2 道菜共享的食材节点 | **0** | 连通性判定退化为查表 |
| 不同食材名 | **759**（含元节点）/ 758（真实） | Day 3 合并目标 |
| 按 name 合并后跨 ≥2 道菜的名字 | **268** | 合并后才有真实多跳 |
| 含 ≥2 种食材的菜谱 | 320 / 322 | 两跳任务候选池 |
| REQUIRES 有 amount 的关系 | 2896 / 2905 | 99.7%，数据质量良好 |

> **这组数是 Day 3 的判据**：合并后「被 ≥2 道菜共享的食材」必须从 0 变成 268，
> 「不同食材名」必须从 759 降到 758 附近。达不到说明合并逻辑有问题。

## 与计划的差异

| 计划假设 | 实际 | 处理 |
| --- | --- | --- |
| GPU 16 GB | T4 15 GB，compute 7.5 | 模型降级到 3B/4bit，Day 2 调整 |
| 本机开发 + 服务器执行 | 服务器为容器环境，root 用户 | 无影响 |
| Java 17/21 | Java 20 | 仅在 Docker 不可用走 tarball 时才需处理 |

## 复现命令

```bash
bash scripts/day01_env_check.sh 2>&1 | tee results/day01_env_check.txt
bash scripts/day01_db_check.sh  2>&1 | tee results/day01_db_check.txt
```
