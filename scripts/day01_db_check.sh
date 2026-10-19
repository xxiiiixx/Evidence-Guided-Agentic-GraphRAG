#!/usr/bin/env bash
# Day 1 数据库自检 —— 启动 Neo4j、导入 C9 数据、统计节点与关系
#
# 用法：
#   bash scripts/day01_db_check.sh             # 默认 7687 端口
#   NEO4J_PORT=7688 bash scripts/day01_db_check.sh
#
# 输出：results/neo4j_stats.json（机器可读）+ 屏幕统计表（人工核对）
#
# 设计原则：
#   - 幂等：重复跑不会重复导入数据（导入脚本用 MERGE）
#   - 失败即停并给出诊断，不做静默降级
#   - 统计值与计划里的预期值对比，差异显式打印

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p results

NEO4J_PORT="${NEO4J_PORT:-7687}"
NEO4J_URI="${NEO4J_URI:-bolt://localhost:${NEO4J_PORT}}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-all-in-rag}"
CONTAINER="${NEO4J_CONTAINER:-neo4j-db}"

print_rollback() {
  cat <<'EOF'

如果这里失败，先别改代码。按顺序排查：
  1. docker ps -a            看 neo4j-db / neo4j-init 状态
  2. docker logs neo4j-db    看 Neo4j 是否 OOM 或端口占用
  3. docker logs neo4j-init  看导入脚本报什么错
  4. ss -lntp | grep 7687    看端口是否被别的进程占了

常见原因：
  - 端口冲突：         改 NEO4J_PORT 环境变量
  - WSL/容器内存不足： docker compose 里给 neo4j 加 heap 上限
  - 导入脚本路径错：   compose 里 ./cypher 挂到 /import，需在 data/C9 目录下执行
EOF
}

hr() { printf '\n=== %s ===\n' "$1"; }

# ---------------------------------------------------------------- 0. 前置检查
hr "0. 前置检查"
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker 未安装。参见 28_day_plan.md §11 的降级路径（tarball + JRE）。"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose 不可用（可能是旧的 docker-compose v1）。"
  exit 1
fi
echo "docker OK: $(docker --version)"

if [ ! -f data/C9/docker-compose.yml ]; then
  echo "ERROR: 找不到 data/C9/docker-compose.yml，请在仓库根目录执行。"
  exit 1
fi

# ---------------------------------------------------------------- 1. 启动
hr "1. 启动 Neo4j（compose 在 data/C9 下，./cypher 是相对路径挂载）"
( cd data/C9 && docker compose up -d ) || { echo "compose up FAILED"; print_rollback; exit 1; }

# ---------------------------------------------------------------- 2. 等健康
hr "2. 等待为 neo4j-db healthy（最多 180s）"
for i in $(seq 1 36); do
  status=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
  printf '[%02d/36] health=%s\n' "$i" "$status"
  [ "$status" = "healthy" ] && break
  [ "$status" = "missing" ] && { echo "ERROR: 找不到容器 $CONTAINER"; print_rollback; exit 1; }
  sleep 5
done
if [ "${status:-}" != "healthy" ]; then
  echo "ERROR: 180s 内未 healthy。"
  docker logs --tail 40 "$CONTAINER" 2>&1
  print_rollback
  exit 1
fi

# ---------------------------------------------------------------- 3. 等导入
hr "3. 等待 neo4j-init 导入完成"
for i in $(seq 1 60); do
  state=$(docker inspect --format='{{.State.Status}}' neo4j-init 2>/dev/null || echo "missing")
  code=$(docker inspect --format='{{.State.ExitCode}}' neo4j-init 2>/dev/null || echo "-")
  printf '[%02d/60] neo4j-init status=%s exit=%s\n' "$i" "$state" "$code"
  if [ "$state" = "exited" ]; then
    if [ "$code" = "0" ]; then
      echo "导入完成，exit=0"
    else
      echo "ERROR: 导入失败 exit=$code"
      docker logs --tail 60 neo4j-init 2>&1
      print_rollback
      exit 1
    fi
    break
  fi
  sleep 5
done

# ---------------------------------------------------------------- 4. 统计
hr "4. 统计查询"
CY() {
  docker exec "$CONTAINER" cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
    --format plain "$1" 2>&1 | grep -v '^$'
}

echo "--- 节点 ---"
NODES=$(CY "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC;")
echo "$NODES"

echo "--- 关系 ---"
RELS=$(CY "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS cnt ORDER BY cnt DESC;")
echo "$RELS"

echo "--- 每条 REQUIRES 的 amount 是否有值（数据质量）---"
CY "MATCH (:Recipe)-[r:REQUIRES]->(:Ingredient)
    RETURN count(*) AS total,
           sum(CASE WHEN r.amount IS NOT NULL AND r.amount <> '' THEN 1 ELSE 0 END) AS with_amount;"

echo "--- 含 >=2 种食材的菜谱数（两跳任务的候选池）---"
CY "MATCH (r:Recipe)-[:REQUIRES]->(i:Ingredient)
    WITH r, count(DISTINCT i) AS n WHERE n >= 2
    RETURN count(r) AS recipes_with_2plus_ingredients;"

echo "--- 采样一条真实路径（用于 path_samples 格式确认）---"
CY "MATCH p = (r:Recipe)-[:REQUIRES]->(i:Ingredient)
    RETURN r.nodeId AS recipe_id, r.name AS recipe_name,
           i.nodeId AS ingredient_id, i.name AS ingredient_name
    LIMIT 5;"

# ---------------------------------------------------------------- 5. 落盘
hr "5. 写入 results/neo4j_stats.json"
python3 - "$NODES" "$RELS" <<'PY'
import json, sys, subprocess, datetime, os

nodes_raw, rels_raw = sys.argv[1], sys.argv[2]
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

def parse(block):
    out = {}
    for line in block.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out

data = {
    "captured_at": datetime.datetime.now().astimezone().isoformat(),
    "source_commit": commit or None,
    "neo4j_uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
    "nodes": parse(nodes_raw),
    "relationships": parse(rels_raw),
    "expected_from_csv": {
        "nodes": {
            "Ingredient": 2906, "CookingStep": 2514, "Recipe": 323,
            "RecipeCategory": 9, "DifficultyLevel": 5,
            "Root": 1, "CookingMethod": 1, "CookingTool": 1
        },
        "relationships": {"REQUIRES": 2905, "CONTAINS_STEP": 2514, "BELONGS_TO": 314, "DIFFICULTY_LEVEL": 322},
        "note": "BELONGS_TO/DifficultyLevel 对应关系已在 neo4j_import.cypher:198-199 核对；"
                "Root 会被导入两次（导入脚本第二个分支缺 ID 下界），计为 2 属已知行为"
    }
}
with open("results/neo4j_stats.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(json.dumps(data, ensure_ascii=False, indent=2))
PY

hr "6. 与预期值对比"
python3 - <<'PY'
import json
with open("results/neo4j_stats.json", encoding="utf-8") as f:
    d = json.load(f)
exp = d["expected_from_csv"]
print(f"{'类型':<20}{'实际':>8}{'预期':>8}{'差异':>8}")
print("-" * 44)
for kind in ("nodes", "relationships"):
    for k, v in exp[kind].items():
        actual = d[kind].get(k, 0)
        diff = actual - v
        flag = "" if diff == 0 else ("  <-- 检查" if abs(diff) > 1 else "  (元节点)")
        print(f"{k:<20}{actual:>8}{v:>8}{diff:>+8}{flag}")
PY

hr "done"
echo "产物: results/neo4j_stats.json"
echo "请把第 4 节的完整输出 + 第 6 节对比表贴回给导师"
