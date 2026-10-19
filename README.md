# Evidence-Guided Agentic GraphRAG

基于 [Datawhale / all-in-rag](https://github.com/datawhalechina/all-in-rag) 的 **C9（GraphRAG）** 教学项目进行二次开发，目标是在真实、可复现的检索任务中研究 Agent 的证据反馈决策与 GraphRAG 的图路径证据检索。

> **当前状态：原始 C9 核心代码抽取阶段（尚未完成环境复现或算法改进）。** 此版本不代表已经验证可运行、也不代表任何性能收益。

## 文档导航

| 文档 | 作用 | 状态 |
| --- | --- | --- |
| [计划评审](./docs/plan_review.md) | 对原 28 天计划与 Day 1 工作的逐条审查，含三个被遗漏的架构级问题 | 2026-10-19 |
| [28 天计划 v2](./docs/plan_v2.md) | **当前执行依据**，替代 `28_day_plan.md` | 进行中 |
| [面试官手册](./docs/interviewer_guide.md) | 20 道模拟面试题与追问路径，每周自查 | 持续更新 |
| [Day 1 Baseline 分析](./docs/baseline_analysis.md) | 源码级缺陷梳理（历史记录，部分结论已被评审修正） | 2026-10-15 |
| [原 28 天计划](./docs/28_day_plan.md) | 历史记录，不再作为执行依据 | 已归档 |

## 项目结构

- `main.py` / `config.py`：原 C9 入口与配置。
- `rag_modules/`：原 C9 数据准备、图索引、混合检索、图检索、智能查询路由、答案生成模块。
- `data/C9/`：原 C9 的 Neo4j Compose、Cypher 和 CSV 示例数据。
- `.env.example` / `requirements.txt`：原 C9 的配置样例与 Python 依赖。

此阶段**保持 C9 核心 Python 算法代码原样**，不混入后续策略改进。原 C9 下 `agent(代码系ai生成)/` 是独立示例，未被 `main.py` 依赖，因此没有纳入精简 Baseline；需要时可通过下方上游 commit 找回。

## 上游来源与许可

- 来源：`https://github.com/datawhalechina/all-in-rag`
- 上游 Commit：[`cec956e80ae28d6c0ca84c9d39dddcf61ad633fd`](https://github.com/datawhalechina/all-in-rag/commit/cec956e80ae28d6c0ca84c9d39dddcf61ad633fd)
- 抽取范围：`code/C9/` 的主要运行文件，`data/C9/` 的数据库导入与部署文件。
- 原仓库 README 声明使用 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans)，相关许可及作者归属请参见 [UPSTREAM.md](./UPSTREAM.md)。
- 原项目：Datawhale 和原贡献者；此仓库后续改动与评测将明确区分原始实现和个人修改。

## 运行前准备（尚待验证）

1. 参考 `data/C9/docker-compose.yml` 启动 Neo4j，并导入 `data/C9/cypher/` 示例数据。
2. 使用合适的 Python 环境安装 `requirements.txt` 的依赖。
3. 将 `.env.example` 复制为 `.env`，按实际环境配置模型 API Key 和数据库连接（**不要提交 .env**）。
4. 准备 Milvus 以及 `BAAI/bge-small-zh-v1.5` 模型，运行 `python main.py` 前核对原项目代码的运行入口。

**安全提示：** 上游演示的 Neo4j 密码和 Docker Compose 配置只适合隔离的本地测试环境，不要直接暴露给公网。

## 28 天改进主线

- **Agent**：将一次性路由改进为可观察证据、受预算约束的补检索和停止决策。
- **RAG**：完善图路径证据验证、跨检索源证据融合和来源追溯。
- **实验**：保存原始 Baseline，构建固定测试集，比较成功率、证据质量、时延与工具调用成本，明确记录消融和失败案例。

具体实验指标与结论须在真实运行后填入；尚无性能数据。
