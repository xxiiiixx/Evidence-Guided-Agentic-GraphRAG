#!/usr/bin/env bash
# Day 3 重建图自检 —— 启动独立 Neo4j 实例、导入重建图、核对「库里的图」与「CSV 算出的图」
#
# 用法（在仓库根目录或任意位置都行，脚本自己 cd 到根目录）：
#   bash data/rebuild/day03_db_check.sh
#   FORCE_REIMPORT=1 bash data/rebuild/day03_db_check.sh   # CSV 改过、要重新导入时
#   NEO4J_PORT=7688 NEO4J_PASSWORD=all-in-rag bash data/rebuild/day03_db_check.sh
#
# 它回答的问题：
#   1. 重建图能不能真的导进 Neo4j（导入脚本不是只做过静态检查吗？）
#   2. 导进去的节点/关系数，和 data/rebuild/*.csv 逐行数出来的是否一致
#   3. day03.md 里那些「重建后」的数字（255、178.0、59407、222…）在真实库里是否成立
#
# 设计原则：
#   - 期望值不写死，现场从 data/rebuild/*.csv 用纯标准库重新算一遍（服务器不需要 pandas）
#   - 只把「计划里的验收锚点」写死，用来抓 CSV 本身的回归
#   - 失败即停并给出诊断，不做静默降级

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
mkdir -p results

CONTAINER="${NEO4J_CONTAINER:-neo4j-rebuild}"
INIT_CONTAINER="${NEO4J_INIT_CONTAINER:-neo4j-rebuild-init}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-all-in-rag}"
COMPOSE_FILE="data/rebuild/docker-compose.yml"

hr() { printf '\n=== %s ===\n' "$1"; }

print_rollback() {
  cat <<'EOF'

如果这里失败，先别改代码。按顺序排查：
  1. docker ps -a                   看 neo4j-rebuild / neo4j-rebuild-init 状态
  2. docker logs neo4j-rebuild      看 Neo4j 是否 OOM 或端口占用
  3. docker logs neo4j-rebuild-init 看导入脚本报什么错
  4. ss -lntp | grep -E '7475|7688' 看端口是否被别的进程占了

常见原因：
  - 端口冲突：        改 docker-compose.yml 里的 7475/7688
  - 容器内存不足：    Neo4j 默认 heap 可能不够，调 NEO4J_server_memory_heap_max__size
  - CSV 没挂进去：    脚本用 ./:/import 挂载本目录，必须存在 data/rebuild/nodes.csv
  - 想重跑导入：      FORCE_REIMPORT=1 再执行本脚本
  - 想彻底重来：      docker compose -f data/rebuild/docker-compose.yml down -v
EOF
}

# ---------------------------------------------------------------- 0. 前置检查
hr "0. 前置检查"
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker 未安装。本脚本必须在有 Docker 的机器上跑（通常是你的 Linux 服务器）。"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose 不可用（可能是旧的 docker-compose v1）。"
  exit 1
fi
echo "docker OK: $(docker --version)"

for f in "$COMPOSE_FILE" data/rebuild/neo4j_import.cypher \
         data/rebuild/nodes.csv data/rebuild/relationships.csv; do
  if [ ! -f "$f" ]; then
    echo "ERROR: 找不到 $f，请在仓库根目录执行（当前 $ROOT）。"
    exit 1
  fi
done
echo "输入文件 OK: 4/4"

# ---------------------------------------------------------------- 1. 启动
hr "1. 启动独立 Neo4j 实例（$COMPOSE_FILE）"
( docker compose -f "$COMPOSE_FILE" up -d ) || { echo "compose up FAILED"; print_rollback; exit 1; }

if [ "${FORCE_REIMPORT:-0}" = "1" ]; then
  echo "FORCE_REIMPORT=1：重建导入容器"
  docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$INIT_CONTAINER" \
    || { echo "force-recreate FAILED"; print_rollback; exit 1; }
fi

# ---------------------------------------------------------------- 2. 等健康
hr "2. 等待 $CONTAINER healthy（最多 180s）"
status="missing"
for i in $(seq 1 36); do
  status=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
  printf '[%02d/36] health=%s\n' "$i" "$status"
  [ "$status" = "healthy" ] && break
  if [ "$status" = "missing" ]; then
    echo "ERROR: 找不到容器 $CONTAINER"
    print_rollback
    exit 1
  fi
  sleep 5
done
if [ "$status" != "healthy" ]; then
  echo "ERROR: 180s 内未 healthy。"
  docker logs --tail 40 "$CONTAINER" 2>&1
  print_rollback
  exit 1
fi

# ---------------------------------------------------------------- 3. 等导入
hr "3. 等待 $INIT_CONTAINER 导入完成"
init_ok=0
for i in $(seq 1 90); do
  state=$(docker inspect --format='{{.State.Status}}' "$INIT_CONTAINER" 2>/dev/null || echo "missing")
  code=$(docker inspect --format='{{.State.ExitCode}}' "$INIT_CONTAINER" 2>/dev/null || echo "-")
  printf '[%02d/90] init status=%s exit=%s\n' "$i" "$state" "$code"
  if [ "$state" = "exited" ]; then
    if [ "$code" = "0" ]; then
      echo "导入完成，exit=0"
      init_ok=1
    else
      echo "ERROR: 导入失败 exit=$code"
      docker logs --tail 60 "$INIT_CONTAINER" 2>&1
      print_rollback
      exit 1
    fi
    break
  fi
  sleep 3
done
if [ "$init_ok" != "1" ]; then
  echo "ERROR: 270s 内导入未结束。"
  docker logs --tail 60 "$INIT_CONTAINER" 2>&1
  print_rollback
  exit 1
fi

# ---------------------------------------------------------------- 4. 采集
hr "4. 采集库里的指标"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
DB="$TMP/db.tsv"
: > "$DB"

CY() {
  docker exec "$CONTAINER" cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
    --format plain "$1" 2>&1 | tr -d '\r'
}

# 取标量：丢掉表头与空行，取最后一行，剥掉 cypher-shell 加的双引号
scalar() {
  CY "$1" | grep -v '^[[:space:]]*$' | tail -n +2 | tail -n 1 \
    | sed -e 's/^"//' -e 's/"$//' -e 's/[[:space:]]*$//'
}

rec() { printf '%s\t%s\n' "$1" "${2:-}" >> "$DB"; }

echo "--- 节点（按 label）---"
NODES_BLOCK=$(CY "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt ORDER BY cnt DESC;")
printf '%s\n' "$NODES_BLOCK"
printf '%s\n' "$NODES_BLOCK" > "$TMP/nodes.txt"

echo "--- 关系（按 type）---"
RELS_BLOCK=$(CY "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS cnt ORDER BY cnt DESC;")
printf '%s\n' "$RELS_BLOCK"
printf '%s\n' "$RELS_BLOCK" > "$TMP/rels.txt"

echo "--- 其余指标（逐条查询）---"
n=0
add() {
  local v
  v=$(scalar "$2")
  rec "$1" "$v"
  n=$((n + 1))
  # 非数值 = 查询报错了。当场喊出来，否则只会在第 5 节显示成一个看不懂的 MISMATCH。
  if [[ "$v" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
    printf '  %-28s ok\n' "$1"
  else
    printf '  %-28s WARN 非数值返回: %s\n' "$1" "${v:0:120}"
  fi
}

add nodes_total              "MATCH (n) RETURN count(n);"
add rels_total               "MATCH ()-[r]->() RETURN count(r);"
add distinct_ingredient_names "MATCH (i:Ingredient) RETURN count(DISTINCT i.name);"
add shared_ingredients       "MATCH (i:Ingredient)<-[:REQUIRES]-(r:Recipe) WITH i, count(DISTINCT r) AS c WHERE c >= 2 RETURN count(i);"
add requires_isMain          "MATCH ()-[r:REQUIRES]->() WHERE r.isMain RETURN count(r);"
add recipes_without_category "MATCH (r:Recipe) WHERE NOT (r)-[:BELONGS_TO]->(:RecipeCategory) RETURN count(r);"
add unused_categories        "MATCH (c:RecipeCategory) WHERE NOT ()-[:BELONGS_TO]->(c) RETURN count(c);"
add multi_label_nodes        "MATCH (n) WHERE size(labels(n)) <> 1 RETURN count(n);"
add relationshipid_distinct  "MATCH ()-[r]->() RETURN count(DISTINCT r.relationshipId);"
add orphan_steps             "MATCH (s:CookingStep) WHERE NOT ()-[:CONTAINS_STEP]->(s) RETURN count(s);"
add recipes_without_ingredient "MATCH (r:Recipe) WHERE NOT (r)-[:REQUIRES]->(:Ingredient) RETURN count(r);"
add dropped_nodes_absent     "MATCH (n) WHERE n.nodeId IN ['200000000','300000000','201000066','201000833','201000869','201004002'] RETURN count(n);"
# 中文名一律写成 \uXXXX 转义：中文直接进 argv 时，cypher-shell 所在 JVM 若没有
# UTF-8 locale，会按 ASCII 解码命令行，字面量变乱码、匹配恒为 0（第一次跑就踩了）。
add chenpi_recipe_count      "MATCH (r:Recipe) WHERE r.name = '\u9648\u76AE\u6392\u9AA8\u6C64' RETURN count(r);"
add chenpi_by_id             "MATCH (r:Recipe {nodeId:'201003873'}) RETURN count(r);"
add ing_name_chars_total     "MATCH (i:Ingredient) RETURN sum(size(i.name));"
add ing_name_maxlen          "MATCH (i:Ingredient) RETURN max(size(i.name));"
add constraints_count        "SHOW CONSTRAINTS YIELD name RETURN count(*);"
add fulltext_indexes         "SHOW FULLTEXT INDEXES YIELD name RETURN count(*);"

# 连通性：1 跳（经过食材）能到达多少道别的菜
CONN_ALL="MATCH (r:Recipe) OPTIONAL MATCH (r)-[:REQUIRES]->(:Ingredient)<-[:REQUIRES]-(o:Recipe) WHERE o <> r WITH r, count(DISTINCT o) AS reach "
add conn_all_total    "${CONN_ALL} RETURN count(r);"
add conn_all_isolated "${CONN_ALL} RETURN sum(CASE WHEN reach = 0 THEN 1 ELSE 0 END);"
add conn_all_mean     "${CONN_ALL} RETURN round(avg(reach), 4);"
add conn_all_median   "${CONN_ALL} RETURN percentileCont(reach, 0.5);"
add conn_all_max      "${CONN_ALL} RETURN max(reach);"

CONN_MAIN="MATCH (r:Recipe) OPTIONAL MATCH (r)-[a:REQUIRES]->(:Ingredient)<-[b:REQUIRES]-(o:Recipe) WHERE a.isMain AND b.isMain AND o <> r WITH r, count(DISTINCT o) AS reach "
add conn_main_total    "${CONN_MAIN} RETURN count(r);"
add conn_main_isolated "${CONN_MAIN} RETURN sum(CASE WHEN reach = 0 THEN 1 ELSE 0 END);"
add conn_main_mean     "${CONN_MAIN} RETURN round(avg(reach), 4);"
add conn_main_median   "${CONN_MAIN} RETURN percentileCont(reach, 0.5);"
add conn_main_max      "${CONN_MAIN} RETURN max(reach);"

# 两菜共享食材：行数（同一对菜共享多种食材会重复计）、去重对数、覆盖菜谱数
PAIR="MATCH (a:Recipe)-[:REQUIRES]->(:Ingredient)<-[:REQUIRES]-(b:Recipe) WHERE a.nodeId < b.nodeId "
add pair_rows_unordered "${PAIR} RETURN count(*);"
add pair_unique         "${PAIR} RETURN count(DISTINCT a.nodeId + '|' + b.nodeId);"
add pair_cover_recipes  "${PAIR} WITH DISTINCT a, b UNWIND [a, b] AS x RETURN count(DISTINCT x.nodeId);"
add pair_rows_directed  "MATCH (a:Recipe)-[:REQUIRES]->(:Ingredient)<-[:REQUIRES]-(b:Recipe) WHERE a.nodeId <> b.nodeId RETURN count(*);"

# 咖喱炒蟹出发的可达数
add crab_reach      "MATCH (a:Recipe {nodeId:'201000001'})-[:REQUIRES]->(:Ingredient)<-[:REQUIRES]-(b:Recipe) WHERE b.nodeId <> a.nodeId RETURN count(DISTINCT b);"
add crab_reach_main "MATCH (a:Recipe {nodeId:'201000001'})-[x:REQUIRES]->(:Ingredient)<-[y:REQUIRES]-(b:Recipe) WHERE x.isMain AND y.isMain AND b.nodeId <> a.nodeId RETURN count(DISTINCT b);"

# 几个头部食材被多少道菜用到（同义词归一是否真的生效）
# 第二列是 \uXXXX 转义后的名字，原因同上：不能让中文进 argv。
while read -r ing esc; do
  add "use_$ing" "MATCH (i:Ingredient {name:'$esc'})<-[:REQUIRES]-(r:Recipe) RETURN count(DISTINCT r);"
done <<'PAIRS'
生抽 \u751F\u62BD
食用油 \u98DF\u7528\u6CB9
盐 \u76D0
大蒜 \u5927\u849C
料酒 \u6599\u9152
鸡蛋 \u9E21\u86CB
PAIRS

echo "共采集 $n 项标量指标（+2 张分组表）"

# ---------------------------------------------------------------- 5. 对比
hr "5. 与 CSV 逐行算出的期望值对比"
python3 - "$DB" "$TMP" "$ROOT" <<'PY'
import csv, os, sys
from collections import Counter, defaultdict
from itertools import combinations

db_path, tmp, root = sys.argv[1], sys.argv[2], sys.argv[3]
base = os.path.join(root, "data", "rebuild")

# ---------- 读库里的实测值 ----------
actual = {}
with open(db_path, encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        k, _, v = line.partition("\t")
        actual[k] = v


def parse_block(path):
    """解析 cypher-shell --format plain 的两列表格（表头 + 若干行）。"""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip().strip('"').strip("'") for p in line.split(",")]
            if len(parts) < 2:
                continue
            key, val = parts[0], parts[1]
            if key.lower() in ("label", "type"):
                continue
            if val.isdigit():
                out[key] = int(val)
    return out


for label, cnt in parse_block(os.path.join(tmp, "nodes.txt")).items():
    actual[f"node_{label}"] = str(cnt)
for t, cnt in parse_block(os.path.join(tmp, "rels.txt")).items():
    actual[f"rel_{t}"] = str(cnt)

# ---------- 从 CSV 重新算期望值（只用标准库，服务器不需要 pandas）----------
def read_csv(name):
    with open(os.path.join(base, name), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


nodes = read_csv("nodes.csv")
rels = read_csv("relationships.csv")

TYPE = {"801000001": "REQUIRES", "801000003": "CONTAINS_STEP",
        "801000004": "BELONGS_TO", "801000005": "DIFFICULTY_LEVEL"}

label_of = {n["nodeId"]: n["labels"] for n in nodes}
name_of = {n["nodeId"]: n["name"] for n in nodes}

label_counts = Counter(n["labels"] for n in nodes)
rel_counts = Counter(TYPE.get(r["relationshipType"], r["relationshipType"]) for r in rels)

recipes = {n["nodeId"] for n in nodes if n["labels"] == "Recipe"}
ingredients = {n["nodeId"] for n in nodes if n["labels"] == "Ingredient"}
categories = {n["nodeId"] for n in nodes if n["labels"] == "RecipeCategory"}

req = [r for r in rels if r["relationshipType"] == "801000001"]
req_main = [r for r in req if r.get("isMain") == "True"]
belongs = [r for r in rels if r["relationshipType"] == "801000004"]
steps = [r for r in rels if r["relationshipType"] == "801000003"]

by_ing = defaultdict(set)
by_ing_main = defaultdict(set)
for r in req:
    by_ing[r["endNodeId"]].add(r["startNodeId"])
for r in req_main:
    by_ing_main[r["endNodeId"]].add(r["startNodeId"])

shared = sum(1 for v in by_ing.values() if len(v) >= 2)

# 两菜共享食材的三种口径
pair_rows_unordered = sum(len(v) * (len(v) - 1) // 2 for v in by_ing.values())
pair_rows_directed = sum(len(v) * (len(v) - 1) for v in by_ing.values())
unique_pairs = set()
for v in by_ing.values():
    for a, b in combinations(sorted(v), 2):
        unique_pairs.add((a, b))
pair_cover = len({x for p in unique_pairs for x in p})


def conn_stats(map_ing):
    reach = {r: set() for r in recipes}
    for v in map_ing.values():
        for a, b in combinations(sorted(v), 2):
            reach[a].add(b)
            reach[b].add(a)
    vals = [len(reach[r]) for r in recipes]
    vals_sorted = sorted(vals)
    k = len(vals_sorted)
    med = vals_sorted[k // 2] if k % 2 else (vals_sorted[k // 2 - 1] + vals_sorted[k // 2]) / 2
    return {
        "total": k,
        "isolated": sum(1 for x in vals if x == 0),
        "mean": round(sum(vals) / k, 4),
        "median": med,
        "max": max(vals),
    }


ca = conn_stats(by_ing)
cm = conn_stats(by_ing_main)

crab = "201000001"
crab_reach = len(set().union(*[by_ing[i] for i in by_ing if crab in by_ing[i]]) - {crab}) \
    if any(crab in v for v in by_ing.values()) else 0
crab_reach_main = len(set().union(*[by_ing_main[i] for i in by_ing_main if crab in by_ing_main[i]]) - {crab}) \
    if any(crab in v for v in by_ing_main.values()) else 0

use_count = {}
for ing, rs in by_ing.items():
    use_count[name_of.get(ing, "")] = len(rs)

recipe_with_cat = {r["startNodeId"] for r in belongs}
cat_used = {r["endNodeId"] for r in belongs}
step_used = {r["endNodeId"] for r in steps}
recipe_with_ing = {r["startNodeId"] for r in req}
all_steps = {n for n in name_of if label_of[n] == "CookingStep"}

expected = {
    "nodes_total": len(nodes),
    "rels_total": len(rels),
    "distinct_ingredient_names": len({name_of[i] for i in ingredients}),
    "shared_ingredients": shared,
    "requires_isMain": len(req_main),
    "recipes_without_category": len(recipes - recipe_with_cat),
    "unused_categories": len(categories - cat_used),
    "multi_label_nodes": 0,                       # CSV 每行只有一个 label
    "relationshipid_distinct": len({r["relationshipId"] for r in rels}),
    "orphan_steps": len(all_steps - step_used),
    "recipes_without_ingredient": len(recipes - recipe_with_ing),
    "dropped_nodes_absent": sum(1 for n in nodes if n["nodeId"] in {
        "200000000", "300000000", "201000066", "201000833", "201000869", "201004002"}),
    "chenpi_recipe_count": sum(1 for n in nodes
                               if n["labels"] == "Recipe" and n["name"] == "陈皮排骨汤"),
    "chenpi_by_id": sum(1 for n in nodes if n["nodeId"] == "201003873"),
    # 名字总字符数 / 最长名字：若导入时编码坏了，一个 2 字名会变成 6 个字符，这两项立刻露馅
    "ing_name_chars_total": sum(len(name_of[i]) for i in ingredients),
    "ing_name_maxlen": max(len(name_of[i]) for i in ingredients),
    "constraints_count": 8,                       # 由 neo4j_import.cypher 决定，不是 CSV 属性
    "fulltext_indexes": 3,
    "conn_all_total": ca["total"],
    "conn_all_isolated": ca["isolated"],
    "conn_all_mean": ca["mean"],
    "conn_all_median": ca["median"],
    "conn_all_max": ca["max"],
    "conn_main_total": cm["total"],
    "conn_main_isolated": cm["isolated"],
    "conn_main_mean": cm["mean"],
    "conn_main_median": cm["median"],
    "conn_main_max": cm["max"],
    "pair_rows_unordered": pair_rows_unordered,
    "pair_unique": len(unique_pairs),
    "pair_cover_recipes": pair_cover,
    "pair_rows_directed": pair_rows_directed,
    "crab_reach": crab_reach,
    "crab_reach_main": crab_reach_main,
}
for ing in ("生抽", "食用油", "盐", "大蒜", "料酒", "鸡蛋"):
    expected[f"use_{ing}"] = use_count.get(ing, 0)

for lab, cnt in label_counts.items():
    expected[f"node_{lab}"] = cnt
for t, cnt in rel_counts.items():
    expected[f"rel_{t}"] = cnt

# ---------- 计划里的验收锚点：写死，用来抓 CSV 自身的回归 ----------
ANCHORS = {
    "node_Recipe": 321,
    "node_Ingredient": 746,
    "node_CookingStep": 2507,
    "node_RecipeCategory": 13,
    "rel_REQUIRES": 2892,
    "rel_CONTAINS_STEP": 2507,
    "rel_BELONGS_TO": 327,
    "rel_DIFFICULTY_LEVEL": 321,
    "shared_ingredients": 255,
    "requires_isMain": 650,
    "orphan_steps": 0,
    "recipes_without_category": 0,
    "pair_rows_unordered": 59407,
    "pair_unique": 28563,
    "crab_reach": 222,
}

TOL = {"conn_all_mean": 0.1, "conn_main_mean": 0.1,
       "conn_all_median": 0.6, "conn_main_median": 0.6}


def close(metric, a, b):
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= TOL.get(metric, 0.0)


def fmt(v):
    if v is None:
        return "—"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.4f}"
    except (TypeError, ValueError):
        return str(v)[:40]


rows, bad = [], 0
for metric, exp in expected.items():
    act = actual.get(metric)
    ok = act is not None and close(metric, act, exp)
    if not ok:
        bad += 1
    rows.append((metric, fmt(act), fmt(exp), "CSV", "OK" if ok else "MISMATCH"))

print(f"{'指标':<30}{'库里':>12}{'CSV 期望':>12}  {'来源':<6}结果")
print("-" * 74)
for m, a, e, src, res in rows:
    print(f"{m:<30}{a:>12}{e:>12}  {src:<6}{res}")

print("\n--- 验收锚点（写死在计划里，与 CSV 无关）---")
print(f"{'指标':<30}{'库里':>12}{'计划值':>12}  结果")
print("-" * 68)
anchor_bad = 0
for metric, exp in ANCHORS.items():
    act = actual.get(metric)
    ok = act is not None and close(metric, act, exp)
    if not ok:
        anchor_bad += 1
    print(f"{metric:<30}{fmt(act):>12}{fmt(exp):>12}  {'OK' if ok else 'MISMATCH'}")

print()
if bad == 0 and anchor_bad == 0:
    print("结论：全部一致。Neo4j 里导入出来的图，与 data/rebuild/*.csv 逐行算出的图完全相同。")
    print("      day03.md 里「重建后」那一列的数字，在真实库上成立。")
else:
    print(f"结论：{bad} 项与 CSV 不符、{anchor_bad} 项与验收锚点不符。")
    print("      先看 MISMATCH 的指标名：node_* / rel_* 不符 = 导入漏行；")
    print("      conn_* / pair_* 不符 = 图结构不对（多半是 isMain 或同义词合并没生效）。")

sys.exit(0 if (bad == 0 and anchor_bad == 0) else 1)
PY
RC=$?

# ---------------------------------------------------------------- 6. 落盘
hr "6. 写入 results/day03_db_check.json"
python3 - "$DB" "$TMP" "$ROOT" "$RC" <<'PY'
import json, os, subprocess, sys, datetime

db_path, tmp, root, rc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
actual = {}
with open(db_path, encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if line:
            k, _, v = line.partition("\t")
            actual[k] = v

def parse_block(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip().strip('"').strip("'") for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() not in ("label", "type") and parts[1].isdigit():
                out[parts[0]] = int(parts[1])
    return out

commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
data = {
    "captured_at": datetime.datetime.now().astimezone().isoformat(),
    "source_commit": commit or None,
    "neo4j_container": os.environ.get("NEO4J_CONTAINER", "neo4j-rebuild"),
    "passed": rc == "0",
    "nodes": parse_block(os.path.join(tmp, "nodes.txt")),
    "relationships": parse_block(os.path.join(tmp, "rels.txt")),
    "metrics": actual,
    "note": "期望值由本脚本从 data/rebuild/*.csv 现场重算，不依赖 pandas。"
            "pair_rows_unordered = ΣC(n,2)（同一对菜共享多种食材会重复计），"
            "pair_rows_directed = Σn(n-1)（等价 Cypher 里 a<>b 不带 a<b 过滤的行数），"
            "pair_unique = 去重后的菜谱对数。"
            "中文名在 Cypher 里写成 \\uXXXX 转义，避免 cypher-shell 的 JVM 按 ASCII "
            "解码命令行、把字面量变成乱码而使匹配恒为 0。",
}
out = os.path.join(root, "results", "day03_db_check.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"已写入 {out}")
PY

hr "done"
if [ "$RC" = "0" ]; then
  echo "全部通过。产物: results/day03_db_check.json"
else
  echo "有指标不符，见第 5 节 MISMATCH。产物: results/day03_db_check.json"
fi
echo "请把第 5 节的完整对比表贴回给导师"
exit "$RC"
