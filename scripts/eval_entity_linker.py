"""entity_linker 的标注集评测。

跑法（仓库根目录）：
    python scripts/eval_entity_linker.py

读 data/rebuild/nodes.csv 建词表（不连 Neo4j），跑 21 条标注问句，
逐条对比「应命中的实体 / 主题词」，结果写到 results/entity_link_eval.json。

设计取舍：
- 标注用 (类型, 规范名) 而不是 nodeId —— 判分只关心「找到了哪个实体、当成了菜谱还是食材」。
- 期望里的名字如果不在词表里，记 annotation_error 而不是判错：那说明标注写错了，
  不能让标注错误伪装成链接器失误。
- 允许多命中：问句里出现两个食材就该召回两个，所以只比集合，不要求数量精确相等，
  但多出来的会记在 extra 里供人工看。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any, Dict, List, Set, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_entity_linker():
    """按文件路径加载，绕开 rag_modules/__init__.py 对 neo4j 的依赖。"""
    path = ROOT / "rag_modules" / "entity_linker.py"
    spec = importlib.util.spec_from_file_location("entity_linker_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


el = load_entity_linker()

R = "R"  # Recipe
I = "I"  # Ingredient

# (问句, 期望实体 [(类型, 规范名)], 期望主题词 [规范名], 备注)
CASES: List[Tuple[str, List[Tuple[str, str]], List[str], str]] = [
    ("咖喱炒蟹怎么做？", [(R, "咖喱炒蟹")], [], "Day 2 的固定样本"),
    ("咖喱肥牛需要哪些食材", [(R, "咖喱肥牛")], [], "菜名 + 食材问法"),
    ("红烧猪蹄的做法", [(R, "红烧猪蹄")], [], "「红烧」家族，验证最左最长不拆词"),
    ("红烧茄子怎么烧", [(R, "红烧茄子")], [], ""),
    ("皮蛋瘦肉粥怎么熬", [(R, "皮蛋瘦肉粥")], [], ""),
    ("清蒸生蚝要蒸多久", [(R, "清蒸生蚝")], [], "「多久」不是时长约束，是菜名问法"),
    ("上汤娃娃菜的做法", [(R, "上汤娃娃菜")], [], ""),
    ("酸辣蕨根粉", [(R, "酸辣蕨根粉")], [], "无提示词的裸菜名"),
    ("猪皮冻怎么做", [(R, "猪皮冻")], [], "多分类菜谱（荤菜/凉菜/下酒菜）"),
    ("小龙虾怎么做好吃", [(R, "小龙虾")], [], "菜谱/食材同名 + 「怎么做」提示词 → 取菜谱"),
    ("油泼辣子怎么做", [(R, "油泼辣子")], [], "菜谱/食材同名"),
    ("凉皮和凉粉有什么区别", [(R, "凉皮"), (R, "凉粉")], [], "两组同名一起出现，无提示词 → 默认菜谱 + ambiguous"),
    ("手抓饼", [(R, "手抓饼")], [], "裸同名，默认菜谱"),
    ("鸡蛋和洋葱能一起做吗", [(I, "鸡蛋"), (I, "洋葱")], [], "多食材"),
    ("生抽和老抽有什么区别", [(I, "生抽"), (I, "老抽")], [], "别名表里的近义词对"),
    ("大蒜和生姜", [(I, "大蒜"), (I, "姜")], [], "同义词归一：生姜 → 姜"),
    ("咖喱块和椰浆", [(I, "咖喱块"), (I, "椰浆")], [], ""),
    ("淀粉怎么勾芡", [(I, "淀粉")], [], "同义词归一：生粉 → 淀粉"),
    ("料酒和盐哪个先放", [(I, "料酒")], [], "单字「盐」按设计不进词表，只应命中料酒"),
    ("川菜有哪些", [], ["川菜"], "菜系 → 主题词，不是实体"),
    ("素菜推荐", [], ["素菜"], "菜谱分类 → 主题词"),
]


def expected_nodes(linker, cases) -> List[str]:
    """检查标注里的名字能不能被词表对上（避免标注本身就写错）。

    注意查的是「规范名」而不是「表面形式」：标注写 `姜` 是对的，即使词表里
    只有别名 `生姜` 而没有 `姜` 本身（单字名按设计不进词表）。
    """
    surfaces = set(linker._entries.keys())
    names = {e.name for bucket in linker._entries.values() for e in bucket}
    known = surfaces | names
    missing = []
    for query, ents, topics, _note in cases:
        for _label, name in ents:
            if name not in known:
                missing.append(f"{query} -> 实体 {name}")
        for name in topics:
            if name not in known:
                missing.append(f"{query} -> 主题 {name}")
    return missing


def run() -> Dict[str, Any]:
    linker = el.EntityLinker.from_nodes_csv(str(ROOT / "data" / "rebuild" / "nodes.csv"))
    stats = linker.stats
    missing = expected_nodes(linker, CASES)

    label_letter = {el.RECIPE: R, el.INGREDIENT: I}
    rows: List[Dict[str, Any]] = []
    passed = 0
    for query, exp_ents, exp_topics, note in CASES:
        result = linker.link(query)
        actual: Set[Tuple[str, str]] = {
            (label_letter.get(m.label, m.label), m.name) for m in result.entities
        }
        actual_topics: Set[str] = set(result.topic_names)
        want = set(tuple(x) for x in exp_ents)
        ok = actual == want and actual_topics == set(exp_topics)
        passed += int(ok)
        rows.append(
            {
                "query": query,
                "ok": ok,
                "expected_entities": sorted(f"{l}:{n}" for l, n in want),
                "actual_entities": sorted(f"{l}:{n}" for l, n in actual),
                "extra_entities": sorted(f"{l}:{n}" for l, n in actual - want),
                "missing_entities": sorted(f"{l}:{n}" for l, n in want - actual),
                "expected_topics": sorted(exp_topics),
                "actual_topics": sorted(actual_topics),
                "surfaces": [
                    {
                        "surface": m.surface,
                        "name": m.name,
                        "label": m.label,
                        "source": m.entry.source,
                        "ambiguous": m.ambiguous,
                        "reason": m.reason,
                    }
                    for m in result.entities + result.topics
                ],
                "elapsed_ms": round(result.elapsed_ms, 3),
                "note": note,
            }
        )

    total = len(CASES)
    report = {
        "vocabulary": stats,
        "vocabularySize": linker.vocabulary_size,
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "annotationErrors": missing,
        "elapsedMsTotal": round(sum(r["elapsed_ms"] for r in rows), 3),
        "cases": rows,
    }
    return report


def main() -> int:
    report = run()
    out = ROOT / "results" / "entity_link_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"词表：{' '.join(f'{k}={v}' for k, v in report['vocabulary'].items())}")
    print(f"标注错误：{report['annotationErrors'] or '无'}")
    print()
    for row in report["cases"]:
        flag = "PASS" if row["ok"] else "FAIL"
        print(f"[{flag}] {row['query']}")
        if not row["ok"]:
            print(f"        期望 {row['expected_entities']} / 主题 {row['expected_topics']}")
            print(f"        实际 {row['actual_entities']} / 主题 {row['actual_topics']}")
        print(f"        {row['elapsed_ms']:.3f} ms  " + " ".join(
            f"{s['surface']}->{s['name']}({s['label']})" for s in row["surfaces"]
        ))
    print()
    print(f"准确率 {report['passed']}/{report['total']} = {report['accuracy']:.1%}"
          f"，总耗时 {report['elapsedMsTotal']:.3f} ms")
    print(f"结果已写入 {out.relative_to(ROOT)}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
