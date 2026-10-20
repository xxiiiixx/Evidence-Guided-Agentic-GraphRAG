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
  echo "ERROR: docker 未安装。降级路径：Neo4j 官方 tarball + JRE 17/21（见 docs/28_day_plan.md 附注）。"
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
echo "(中文若显示为 ???，是 cypher-shell 经管道时的 locale 问题，不影响数据本身)"
CY "MATCH p = (r:Recipe)-[:REQUIRES]->(i:Ingredient)
    RETURN r.nodeId AS recipe_id, r.name AS recipe_name,
           i.nodeId AS ingredient_id, i.name AS ingredient_name
    LIMIT 5;"

# ---- 以下是 Day 3 图重建的基线诊断，必须先记录重建前的状态 ----
echo "--- [基线] 每个食材节点属于几道菜（Day 3 合并的判据）---"
CY "MATCH (i:Ingredient)<-[:REQUIRES]-(r:Recipe)
    WITH i, count(DISTINCT r) AS n
    RETURN n AS recipes_per_ingredient, count(*) AS ingredient_nodes
    ORDER BY n;"

echo "--- [基线] 被 >=2 道菜共享的食材节点数（预期 0，若是 0 则连通性=查表）---"
CY "MATCH (i:Ingredient)<-[:REQUIRES]-(r:Recipe)
    WITH i, count(DISTINCT r) AS n WHERE n >= 2
    RETURN count(i) AS ingredients_shared_by_2plus_recipes;"

echo "--- [基线] 按 name 合并后会有多少个真实食材（Day 3 目标值）---"
CY "MATCH (i:Ingredient) WHERE i.name IS NOT NULL
    RETURN count(DISTINCT i.name) AS distinct_ingredient_names, count(i) AS ingredient_nodes;"

echo "--- [基线] 按 name 合并后跨多道菜的真实食材数 ---"
CY "MATCH (i:Ingredient)<-[:REQUIRES]-(r:Recipe)
    WITH i.name AS name, count(DISTINCT r) AS n WHERE n >= 2
    RETURN count(name) AS names_in_2plus_recipes;"

# ---------------------------------------------------------------- 5. 落盘
hr "5. 写入 results/neo4j_stats.json"
python3 - "$NODES" "$RELS" <<'PY'
import json, sys, subprocess, datetime, os

nodes_raw, rels_raw = sys.argv[1], sys.argv[2]
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

def parse(block):
    """解析 cypher-shell --format plain 输出。

    注意：cypher-shell 给字符串列加双引号（如 `"Ingredient", 2906`），
    必须剥掉，否则键会变成 '"Ingredient"'，与预期表的 'Ingredient' 对不上。
    """
    out = {}
    for line in block.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        key = parts[0].strip('"').strip("'").strip()
        val = parts[1].strip('"').strip("'").strip()
        if key and key.lower() != "label" and val.isdigit():
            out[key] = int(val)
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
            "Root": 2, "CookingMethod": 1, "CookingTool": 1,
            "Category": 19, "ConceptType": 8,
            "GraphStatistics": 8, "RelationshipStatistics": 7
        },
        "relationships": {
            "REQUIRES": 2905, "CONTAINS_STEP": 2514,
            "BELONGS_TO": 314, "DIFFICULTY_LEVEL": 322,
            "HAS_CONCEPT_TYPE": 5761, "BELONGS_TO_CATEGORY": 3233,
            "NEXT_STEP": 2199, "SIMILAR": 1000,
            "USES_SAME_TOOL": 1000, "USES_SAME_METHOD": 1000
        },
        "totals": {"nodes": 5803, "relationships": 20248},
        "csv_only": {
            "nodes": 5760,
            "relationships": 6055
        },
        "note": "总数 5803 = CSV 5760 + 脚本派生 42(Category 19/ConceptType 8/"
                "GraphStatistics 8/RelationshipStatistics 7) + Root 重复导入 1。"
                "关系 20248 = CSV 6055 + 脚本派生 14193(HAS_CONCEPT_TYPE/BELONGS_TO_CATEGORY/"
                "NEXT_STEP/SIMILAR/USES_SAME_TOOL/USES_SAME_METHOD)。"
                "Root 重复：两个 LOAD CSV 分支都命中，第二个缺 ID 下界。"
                "Ingredient 2906 含元节点 300000000(食材)，真实食材 2905。"
                "BELONGS_TO/DifficultyLevel 映射见 neo4j_import.cypher:198-199"
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

CSV_NODES = {"Ingredient", "CookingStep", "Recipe", "RecipeCategory",
             "DifficultyLevel", "Root", "CookingMethod", "CookingTool"}
CSV_RELS = {"REQUIRES", "CONTAINS_STEP", "BELONGS_TO", "DIFFICULTY_LEVEL"}

def show(title, kind, csv_keys):
    print(f"\n--- {title} ---")
    print(f"{'类型':<26}{'实际':>8}{'预期':>8}{'差异':>8}  来源")
    print("-" * 66)
    bad = 0
    for k, v in exp[kind].items():
        actual = d[kind].get(k, 0)
        diff = actual - v
        src = "CSV" if k in csv_keys else "脚本派生"
        flag = "" if diff == 0 else "  <-- 检查"
        if diff != 0:
            bad += 1
        print(f"{k:<26}{actual:>8}{v:>8}{diff:>+8}  {src}{flag}")
    return bad

bad = show("节点", "nodes", CSV_NODES)
bad += show("关系", "relationships", CSV_RELS)

print("\n--- 总数 ---")
print(f"{'类型':<26}{'实际':>8}{'预期':>8}{'差异':>8}")
print("-" * 50)
for kind in ("nodes", "relationships"):
    a = sum(d[kind].values())
    v = exp["totals"][kind]
    diff = a - v
    if diff != 0:
        bad += 1
    print(f"{'TOTAL ' + kind:<26}{a:>8}{v:>8}{diff:>+8}{'' if diff == 0 else '  <-- 检查'}")

# 与纯 CSV 层面比对：确认原始数据没丢
print("\n--- 与纯 CSV 行数比对（确认原始数据未丢失）---")
print(f"CSV 原始节点行数          5760 ; 数据库中 CSV 来源节点 {sum(d['nodes'].get(k,0) for k in CSV_NODES)}"
      f" （差 1 = Root 重复导入）")
print(f"CSV 原始关系行数          6055 ; 数据库中 CSV 来源关系 {sum(d['relationships'].get(k,0) for k in CSV_RELS)}")

print()
print(f"结论：{'全部一致' if bad == 0 else f'{bad} 项不符，见 <-- 检查'}")
print("说明：脚本派生节点/关系来自 neo4j_import.cypher 第 215-320 行的自动生成逻辑，属预期行为。")
PY

hr "done"
echo "产物: results/neo4j_stats.json"
echo "请把第 4 节的完整输出 + 第 6 节对比表贴回给导师"
