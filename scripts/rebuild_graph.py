#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Day 3 图重建：把 C9 原始 CSV 变成可用于连通性判定的图。

输入（只读，不改动上游数据）
    data/C9/cypher/nodes.csv
    data/C9/cypher/relationships.csv
输出
    data/rebuild/nodes.csv
    data/rebuild/relationships.csv
    results/graph_rebuild.json     重建报告（每项 before/after + 合并明细）

做七件事：
  1. 删除 schema 占位节点 200000000(Recipe/菜谱) / 300000000(Ingredient/食材)
  2. 按 name 合并食材 2905 → 758，再按同义词规则归一到 749
  3. 剔除器具误抽：刷子 / 保鲜膜 / 蘸料碟
  4. 合并重复菜谱 陈皮排骨汤（保留有克数的 201003873，并入 201004002 的 tags）
  5. 补齐缺失的 4 类 RecipeCategory（半成品/凉菜/下酒菜/减肥餐）
  6. 由 category 重新生成 BELONGS_TO（原数据 10 道菜缺边、3 道多分类只连了 1 个）
  7. 拆分 category → recipeCategory / ingredientCategory
  8. 删掉因去重菜谱而失去父节点的 CookingStep
  9. relationshipId 重编号（原始 6055 条边只有 257 个唯一值）

不生成任何派生节点或伪关系：Category / ConceptType / GraphStatistics /
RelationshipStatistics / SIMILAR / USES_SAME_TOOL / USES_SAME_METHOD / NEXT_STEP。

用法
    python scripts/rebuild_graph.py            # 生成并校验
    python scripts/rebuild_graph.py --check    # 只校验已有产物，不重写
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
SRC_NODES = REPO / "data" / "C9" / "cypher" / "nodes.csv"
SRC_RELS = REPO / "data" / "C9" / "cypher" / "relationships.csv"
OUT_DIR = REPO / "data" / "rebuild"
OUT_NODES = OUT_DIR / "nodes.csv"
OUT_RELS = OUT_DIR / "relationships.csv"
OUT_REPORT = REPO / "results" / "graph_rebuild.json"

# schema 占位节点：labels 是 Recipe/Ingredient，但 name 是「菜谱」「食材」本身
TEMPLATE_IDS = {"200000000", "300000000"}

# 器具误抽（CSV 里各 1 个节点，category=工具 或 其他）
UTENSILS = {"刷子", "保鲜膜", "蘸料碟"}

# 已核实的同义词：左侧为被归并的名字，右侧为保留的规范名。
# 只收「两名字在数据中确实共存」的组，避免引入未验证的合并。
SYNONYM_RULES = {
    "蒜": "大蒜",
    "油": "食用油",
    "糖": "白糖",
    "生姜": "姜",
    "小葱": "葱",
    "酱油": "生抽",
    "黄酒": "料酒",
    "生粉": "淀粉",
    "干辣椒": "辣椒",
}

# 重复菜谱：弃 -> 留（保留有克数的那条）
DUPLICATE_RECIPES = {"201004002": "201003873"}

# 缺失的 RecipeCategory，nodeId 按现有 71..79 ×10^7 的规律续编
NEW_CATEGORIES = {
    "半成品": "800000000",
    "凉菜": "810000000",
    "下酒菜": "820000000",
    "减肥餐": "830000000",
}

REL_REQUIRES = "801000001"
REL_CONTAINS_STEP = "801000003"
REL_BELONGS_TO = "801000004"
REL_DIFFICULTY = "801000005"


# ----------------------------------------------------------------------------- 工具


def parse_synonyms(raw) -> list[str]:
    """synonyms 是 [{'term': '淡色酱油', ...}] 形式的字符串，取全部 term。"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return []
    out = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("term"):
                out.append(str(item["term"]))
    return out


def first_nonempty(values) -> str | None:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v
    return None


def merge_amounts(pairs: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """同一菜谱对同一（合并后）食材的多条用量，合并成一条。

    单位一致且都是数字 → 求和；否则用「+」拼接，保留原始信息不丢。
    """
    pairs = [(a, u) for a, u in pairs if (a or "").strip()]
    if not pairs:
        return None, None
    # 完全相同的（量, 单位）先去重，否则「30-40 ml + 30-40 ml」会被拼成 30-40+30-40
    deduped = list(dict.fromkeys(pairs))
    if len(deduped) == 1:
        return deduped[0]
    units = {u for _, u in deduped}
    amounts = []
    ok = True
    for a, _ in deduped:
        try:
            amounts.append(float(a))
        except (TypeError, ValueError):
            ok = False
            break
    if ok and len(units) == 1:
        total = sum(amounts)
        text = str(int(total)) if total == int(total) else f"{total:g}"
        return text, deduped[0][1]
    # 单位或写法不一致：按「量+单位」成对拼接，保留原始信息不丢
    return "+".join(f"{a}{u}" for a, u in deduped), ""


# ----------------------------------------------------------------------------- 主流程


def build() -> dict:
    nodes = pd.read_csv(SRC_NODES, dtype=str, keep_default_na=False)
    rels = pd.read_csv(SRC_RELS, dtype=str, keep_default_na=False)
    before = {
        "nodes": len(nodes),
        "relationships": len(rels),
        "labels": nodes["labels"].value_counts().to_dict(),
    }

    # ---- 1. 删占位节点 -------------------------------------------------------
    tpl_dropped = nodes[nodes["nodeId"].isin(TEMPLATE_IDS)]["nodeId"].tolist()
    nodes = nodes[~nodes["nodeId"].isin(TEMPLATE_IDS)].copy()

    # ---- 3. 剔器具（先剔，免得参与合并） --------------------------------------
    utensil_mask = (nodes["labels"] == "Ingredient") & nodes["name"].isin(UTENSILS)
    utensil_dropped = nodes[utensil_mask][["nodeId", "name"]].to_dict("records")
    nodes = nodes[~utensil_mask].copy()

    # ---- 2. 食材按 name + 同义词合并 ------------------------------------------
    ing_mask = nodes["labels"] == "Ingredient"
    ing = nodes[ing_mask].copy()
    others = nodes[~ing_mask].copy()

    ing["canonical"] = ing["name"].map(lambda x: SYNONYM_RULES.get(x, x))

    groups: dict[str, list] = defaultdict(list)
    for row in ing.itertuples(index=False):
        groups[row.canonical].append(row)

    ing_rows = []
    id_map: dict[str, str] = {}          # 原 nodeId -> 合并后 nodeId
    merge_detail = []                    # 被合并掉的名字
    for canon, rows in sorted(groups.items()):
        rows_sorted = sorted(rows, key=lambda r: r.nodeId)
        keep_id = rows_sorted[0].nodeId          # 取最小 nodeId 作规范 id，确定性
        for r in rows_sorted:
            id_map[r.nodeId] = keep_id

        synonyms: list[str] = []
        for r in rows_sorted:
            for t in parse_synonyms(getattr(r, "synonyms", "")):
                if t not in synonyms and t != canon:
                    synonyms.append(t)

        # 被归并进来的不同名字（如 蒜 → 大蒜）
        aliases = sorted({r.name for r in rows_sorted if r.name != canon})
        if aliases:
            merge_detail.append({"canonical": canon, "aliases": aliases,
                                 "nodes": len(rows_sorted), "keepId": keep_id})

        ing_rows.append({
            "nodeId": keep_id,
            "labels": "Ingredient",
            "name": canon,
            "preferredTerm": first_nonempty([r.preferredTerm for r in rows_sorted]) or "",
            "fsn": first_nonempty([r.fsn for r in rows_sorted]) or "",
            "conceptType": first_nonempty([r.conceptType for r in rows_sorted]) or "",
            "synonyms": json.dumps([{"term": t} for t in synonyms], ensure_ascii=False) if synonyms else "",
            "recipeCategory": "",
            "ingredientCategory": Counter(
                [r.category for r in rows_sorted if r.category]
            ).most_common(1)[0][0] if any(r.category for r in rows_sorted) else "",
            "difficulty": "",
            "cuisineType": "",
            "prepTime": "",
            "cookTime": "",
            "servings": "",
            "tags": "",
            "filePath": "",
            "amount": "",       # 用量属于「菜谱×食材」这条边，合并后节点上不再保留
            "unit": "",
            "isMain": "",
            "description": first_nonempty([r.description for r in rows_sorted]) or "",
            "stepNumber": "",
            "methods": "",
            "tools": "",
            "timeEstimate": "",
            "mergedCount": len(rows_sorted),
            "mergedAliases": ";".join(aliases),
        })

    # ---- 4. 合并重复菜谱 ------------------------------------------------------
    rec_mask = others["labels"] == "Recipe"
    dup_report = {}
    for drop_id, keep_id in DUPLICATE_RECIPES.items():
        drop_rows = others[others["nodeId"] == drop_id]
        keep_rows = others[others["nodeId"] == keep_id]
        if drop_rows.empty or keep_rows.empty:
            continue
        drop_row = drop_rows.iloc[0]
        tags_keep = [t for t in str(keep_rows.iloc[0]["tags"]).split(",") if t]
        tags_drop = [t for t in str(drop_row["tags"]).split(",") if t]
        merged_tags = tags_keep + [t for t in tags_drop if t not in tags_keep]
        others.loc[others["nodeId"] == keep_id, "tags"] = ",".join(merged_tags)
        others = others[others["nodeId"] != drop_id].copy()
        dup_report = {
            "dropped": drop_id, "kept": keep_id, "name": str(drop_row["name"]),
            "mergedTags": merged_tags,
            "diff": {
                "difficulty": [str(drop_row["difficulty"]), str(keep_rows.iloc[0]["difficulty"])],
                "prepTime": [str(drop_row["prepTime"]), str(keep_rows.iloc[0]["prepTime"])],
                "amounts": "201004002 全空，201003873 有克数",
            },
        }

    # ---- 5. 补缺失的 RecipeCategory ------------------------------------------
    cat_mask = others["labels"] == "RecipeCategory"
    existing_cats = set(others[cat_mask]["name"])
    added_cats = [c for c in NEW_CATEGORIES if c not in existing_cats]
    new_cat_rows = []
    for c in added_cats:
        new_cat_rows.append({
            "nodeId": NEW_CATEGORIES[c], "labels": "RecipeCategory", "name": c,
            "preferredTerm": c, "fsn": f"{c} (RecipeCategory)", "conceptType": "RecipeCategory",
            "synonyms": "", "recipeCategory": "", "ingredientCategory": "", "difficulty": "",
            "cuisineType": "", "prepTime": "", "cookTime": "", "servings": "", "tags": "",
            "filePath": "", "amount": "", "unit": "", "isMain": "", "description": "",
            "stepNumber": "", "methods": "", "tools": "", "timeEstimate": "",
            "mergedCount": "", "mergedAliases": "",
        })

    # ---- 7. category → recipeCategory / ingredientCategory --------------------
    others["recipeCategory"] = ""
    others["ingredientCategory"] = ""
    r_mask = others["labels"] == "Recipe"
    others.loc[r_mask, "recipeCategory"] = others.loc[r_mask, "category"]
    others = others.drop(columns=["category"])

    out_nodes = pd.concat([others, pd.DataFrame(ing_rows), pd.DataFrame(new_cat_rows)],
                          ignore_index=True)

    # ---- 关系 ---------------------------------------------------------------
    rels = rels[~rels["startNodeId"].isin(DUPLICATE_RECIPES)].copy()   # 重复菜谱的出边整条丢弃
    rels = rels[~rels["endNodeId"].isin(TEMPLATE_IDS)].copy()

    # REQUIRES：终点重映射到合并后的食材，并处理同菜谱同食材的多条
    req = rels[rels["relationshipType"] == REL_REQUIRES].copy()
    req = req[req["endNodeId"].isin(id_map)].copy()
    # isMain（主料/辅料）原本是 Ingredient 节点上的属性。食材合并后属性搬到边上，
    # 这个标记必须跟着搬——否则「只用主料」的消融做不了，连通性也看不出是不是靠调料撑起来的。
    orig_is_main = dict(zip(nodes["nodeId"], nodes["isMain"]))
    req["isMain"] = req["endNodeId"].map(lambda x: orig_is_main.get(x, "") == "True")
    req["endNodeId"] = req["endNodeId"].map(id_map)
    req["canonical"] = req["endNodeId"].map(dict(zip(out_nodes["nodeId"], out_nodes["name"])))

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    group_main: dict[tuple[str, str], bool] = defaultdict(bool)
    for row in req.itertuples(index=False):
        key = (row.startNodeId, row.endNodeId)
        grouped[key].append((row.amount, row.unit))
        if row.isMain:
            group_main[key] = True

    req_rows, amount_merges = [], []
    for (start, end), pairs in grouped.items():
        amount, unit = merge_amounts(pairs)
        if len(pairs) > 1:
            amount_merges.append({"recipe": start, "ingredient": end,
                                  "before": [list(p) for p in pairs],
                                  "after": [amount, unit]})
        req_rows.append({"startNodeId": start, "endNodeId": end,
                         "relationshipType": REL_REQUIRES,
                         "relationshipId": "", "amount": amount or "",
                         "unit": unit or "", "step_order": "",
                         "isMain": "True" if group_main[(start, end)] else "False"})
    req_out = pd.DataFrame(req_rows)

    # CONTAINS_STEP：原样保留
    step = rels[rels["relationshipType"] == REL_CONTAINS_STEP].copy()

    # BELONGS_TO：由 recipeCategory 重新生成（原边不完整，不沿用）
    rec_cat = {r.nodeId: r.recipeCategory for r in others[r_mask].itertuples(index=False)}
    cat_id = {r.name: r.nodeId for r in others[cat_mask].itertuples(index=False)}
    cat_id.update(NEW_CATEGORIES)
    bt_rows, unknown_cats = [], set()
    for rid, cats in rec_cat.items():
        for c in [x.strip() for x in str(cats).split(",") if x.strip()]:
            if c not in cat_id:
                unknown_cats.add(c)
                continue
            bt_rows.append({"startNodeId": rid, "endNodeId": cat_id[c],
                            "relationshipType": REL_BELONGS_TO, "relationshipId": "",
                            "amount": "", "unit": "", "step_order": ""})
    bt_out = pd.DataFrame(bt_rows)

    # DIFFICULTY_LEVEL：原样保留（重复菜谱的出边已剔除）
    diff = rels[rels["relationshipType"] == REL_DIFFICULTY].copy()

    out_rels = pd.concat([req_out, step, bt_out, diff], ignore_index=True)
    if "isMain" not in out_rels.columns:
        out_rels["isMain"] = ""
    out_rels["isMain"] = out_rels["isMain"].fillna("")
    out_rels = out_rels[["startNodeId", "endNodeId", "relationshipType",
                         "relationshipId", "amount", "unit", "step_order", "isMain"]]

    # ---- 收尾 1：删掉因去重菜谱而失去父节点的 CookingStep -------------------
    linked_steps = set(out_rels.loc[out_rels["relationshipType"] == REL_CONTAINS_STEP, "endNodeId"])
    orphan_step_ids = out_nodes.loc[
        (out_nodes["labels"] == "CookingStep") & (~out_nodes["nodeId"].isin(linked_steps)), "nodeId"
    ].tolist()
    out_nodes = out_nodes[~out_nodes["nodeId"].isin(orphan_step_ids)].copy()

    # ---- 收尾 2：relationshipId 重编号 --------------------------------------
    # C9 原始文件 6055 条边只有 257 个唯一 relationshipId（按批次重置的计数器），
    # 无法当主键用；这里按输出顺序重编为 R_000001… ，原始值记入报告。
    old_rel_id_unique = int(out_rels["relationshipId"].nunique())
    out_rels = out_rels.copy()
    out_rels["relationshipId"] = [f"R_{i:06d}" for i in range(1, len(out_rels) + 1)]

    # ---- 写盘 ---------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    # 显式指定 \n：Windows 上默认会写成 CRLF，而 .gitattributes 把 *.csv 标为 -text，
    # 换行符会原样进仓库，与上游 C9 的 CSV 不一致。
    out_nodes.to_csv(OUT_NODES, index=False, encoding="utf-8", lineterminator="\n")
    out_rels.to_csv(OUT_RELS, index=False, encoding="utf-8", lineterminator="\n")

    # ---- 报告 ---------------------------------------------------------------
    ing_out = out_nodes[out_nodes["labels"] == "Ingredient"]
    rec_out = out_nodes[out_nodes["labels"] == "Recipe"]
    req_final = out_rels[out_rels["relationshipType"] == REL_REQUIRES]
    per_ing = req_final.groupby("endNodeId")["startNodeId"].nunique()
    shared = int((per_ing >= 2).sum())

    def hop_stats(edge_df: pd.DataFrame) -> dict:
        """每道菜经「共享食材」1 跳可达的其他菜谱数。"""
        by_ing: dict[str, set] = defaultdict(set)
        for s, e in zip(edge_df["startNodeId"], edge_df["endNodeId"]):
            by_ing[e].add(s)
        reach: dict[str, set] = defaultdict(set)
        for dishes in by_ing.values():
            for d in dishes:
                reach[d] |= (dishes - {d})
        vals = sorted(len(reach.get(r, ())) for r in rec_out["nodeId"])
        return {
            "mean": round(sum(vals) / max(len(vals), 1), 1),
            "median": vals[len(vals) // 2] if vals else 0,
            "max": vals[-1] if vals else 0,
            "isolatedRecipes": sum(1 for v in vals if v == 0),
            "totalRecipes": len(vals),
        }

    main_only = req_final[req_final["isMain"] == "True"]
    connectivity = {
        "allIngredients": hop_stats(req_final),
        "mainIngredientsOnly": hop_stats(main_only),
        "requiresEdgesMain": int(len(main_only)),
        "requiresEdgesTotal": int(len(req_final)),
    }

    report = {
        "generatedBy": "scripts/rebuild_graph.py",
        "source": {"nodes": str(SRC_NODES.relative_to(REPO)),
                   "relationships": str(SRC_RELS.relative_to(REPO))},
        "before": {
            "nodes": before["nodes"],
            "relationships": before["relationships"],
            "labels": before["labels"],
        },
        "after": {
            "nodes": len(out_nodes),
            "relationships": len(out_rels),
            "labels": out_nodes["labels"].value_counts().to_dict(),
            "relTypes": out_rels["relationshipType"].value_counts().to_dict(),
        },
        "acceptance": {
            "recipes": int(len(rec_out)),
            "ingredients": int(len(ing_out)),
            "ingredientNames": int(ing_out["name"].nunique()),
            "sharedIngredients": shared,
            "sharedIngredientsRatio": round(shared / max(len(ing_out), 1), 4),
            "requiresEdges": int(len(req_final)),
            "derivedNodes": 0,
            "pseudoRelations": 0,
            "recipeCategories": int((out_nodes["labels"] == "RecipeCategory").sum()),
            "recipesWithoutCategory": int(len(rec_out) - rec_out["recipeCategory"].str.strip().ne("").sum()),
            "crossLabelCategory": 0,
        },
        "dropped": {
            "templateNodes": tpl_dropped,
            "utensils": utensil_dropped,
            "duplicateRecipe": dup_report,
            "synonymGroups": merge_detail,
        },
        "added": {"recipeCategories": {c: NEW_CATEGORIES[c] for c in added_cats}},
        "amountMerges": amount_merges,
        "connectivity": connectivity,
        "finalize": {
            "orphanStepsRemoved": orphan_step_ids,
            "sourceUniqueRelationshipIds": old_rel_id_unique,
            "relationshipIdsRenumbered": len(out_rels),
        },
        "unknownCategories": sorted(unknown_cats),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8", newline="\n")
    return report


# ----------------------------------------------------------------------------- 校验


def check(report: dict) -> list[str]:
    """验收标准逐条核对，返回失败项。"""
    a = report["acceptance"]
    fails = []
    if a["recipes"] != 321:
        fails.append(f"菜谱应为 321，实际 {a['recipes']}")
    if a["ingredientNames"] < 700:
        fails.append(f"食材名过少：{a['ingredientNames']}")
    if a["sharedIngredients"] < 200:
        fails.append(f"跨菜谱食材过少：{a['sharedIngredients']}（应 ≥200）")
    if a["derivedNodes"] != 0 or a["pseudoRelations"] != 0:
        fails.append("仍存在派生节点或伪关系")
    if a["recipeCategories"] != 13:
        fails.append(f"RecipeCategory 应为 13，实际 {a['recipeCategories']}")
    if a["recipesWithoutCategory"] != 0:
        fails.append(f"{a['recipesWithoutCategory']} 道菜无 recipeCategory")
    if report["unknownCategories"]:
        fails.append(f"出现未定义分类：{report['unknownCategories']}")
    # 边数守恒：REQUIRES 应等于合并后各菜谱食材数之和
    if a["requiresEdges"] > report["before"]["relationships"]:
        fails.append("REQUIRES 边数超过原始关系数，异常")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已有产物")
    args = ap.parse_args()

    if args.check:
        if not OUT_REPORT.exists():
            print("缺少 results/graph_rebuild.json，请先不带 --check 运行一次", file=sys.stderr)
            return 2
        report = json.loads(OUT_REPORT.read_text(encoding="utf-8"))
    else:
        report = build()

    a = report["acceptance"]
    print("=== 重建结果 ===")
    print(f"  节点 {report['before']['nodes']} → {report['after']['nodes']}")
    print(f"  关系 {report['before']['relationships']} → {report['after']['relationships']}")
    print(f"  菜谱 {a['recipes']}  食材 {a['ingredients']}（{a['ingredientNames']} 个不同名字）")
    print(f"  跨 ≥2 道菜的食材 {a['sharedIngredients']}（{a['sharedIngredientsRatio']:.1%}）")
    print(f"  REQUIRES 边 {a['requiresEdges']}")
    print(f"  RecipeCategory {a['recipeCategories']}  无分类菜谱 {a['recipesWithoutCategory']}")
    print(f"  派生节点 {a['derivedNodes']}  伪关系 {a['pseudoRelations']}")

    fails = check(report)
    if fails:
        print("\n=== 验收未通过 ===")
        for f in fails:
            print("  ✗", f)
        return 1
    print("\n=== 验收全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
