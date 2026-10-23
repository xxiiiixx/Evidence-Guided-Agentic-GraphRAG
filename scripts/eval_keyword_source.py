"""关键词抽取的评测：闭集提示词能不能把问句映射到图上真实存在的节点。

两种模式：

    python scripts/eval_keyword_source.py                # contract（离线，默认）
    python scripts/eval_keyword_source.py --source llm_vocab   # 真调模型，需要 Key

**contract 模式**不测模型，测的是「管线是否守得住约束」：把标注里的期望名字当作
一个完美模型的回答喂进去，必须 100% 还原成正确的 nodeId；再故意塞一个图上没有的
名字（红烧肉），必须进 dropped、且不影响最终结果。这能证明「模型编出来的名字进不了
检索路径」这条硬约束是代码保证的，而不是靠模型自觉。

**llm_vocab 模式**才是真实准确率：调模型、拿真实回答、比对标注。没有 API Key 时
会明确报错退出，不伪造数字。

名单只从 data/rebuild/nodes.csv 读，不连 Neo4j —— 服务器和本机行为一致。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import types
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODES_CSV = ROOT / "data" / "rebuild" / "nodes.csv"
OUT_JSON = ROOT / "results" / "keyword_source_eval.json"


def load_modules():
    """把 rag_modules 当合成包加载（包里 __init__ 会 import neo4j，本机没装）。"""
    pkg_name = "ragmods_eval"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(ROOT / "rag_modules")]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    loaded = {}
    for name in ("graph_vocabulary", "llm_entity_extractor"):
        full = f"{pkg_name}.{name}"
        spec = importlib.util.spec_from_file_location(full, ROOT / "rag_modules" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["graph_vocabulary"], loaded["llm_entity_extractor"]


gv, lx = load_modules()
R, I, T = gv.RECIPE, gv.INGREDIENT, gv.TOPIC

# 标注集：query → 必须选中的 (label, 规范名)。
# 空数组表示「这个问题里没有可点名的实体」（意图类问句），选不出来是对的。
# 覆盖点：全名命中、别名命中、跨 label 同名、分类/菜系/难度、否定与意图类。
CASES = [
    ("咖喱炒蟹怎么做？", [(R, "咖喱炒蟹"), (I, "青蟹"), (I, "洋葱")]),
    ("咖喱炒蟹要放椰浆吗", [(R, "咖喱炒蟹"), (I, "椰浆")]),
    ("红烧冬瓜的做法", [(R, "红烧冬瓜")]),
    ("西红柿牛腩要放多少盐", [(R, "西红柿牛腩"), (I, "盐")]),
    ("大蒜和姜要一起放吗", [(I, "大蒜"), (I, "姜")]),
    ("生姜放多少", [(I, "姜")]),
    ("生抽和老抽有什么区别", [(I, "生抽"), (I, "老抽")]),
    ("料酒可以去腥吗", [(I, "料酒")]),
    ("蚝油炒菜什么时候放", [(I, "蚝油")]),
    ("白糖和冰糖哪个更甜", [(I, "白糖")]),
    ("淀粉勾芡怎么弄", [(I, "淀粉")]),
    ("牛排要煎几分钟", [(R, "牛排"), (I, "牛排")]),
    ("小龙虾怎么洗", [(R, "小龙虾"), (I, "小龙虾")]),
    ("凉皮是哪里的菜", [(R, "凉皮"), (I, "凉皮")]),
    ("川菜有哪些家常做法", [(T, "川菜")]),
    ("水产类有什么推荐", [(T, "水产")]),
    ("素菜里最简单的", [(T, "素菜")]),
    ("三星难度的菜有哪些", [(T, "三星")]),
    ("粤菜早茶点心", [(T, "粤菜")]),
    ("鸡蛋和什么一起炒好吃", [(I, "鸡蛋")]),
    ("今天想吃点健康的", []),
    ("来点清淡的", []),
    ("有没有快手的菜", []),
    ("家里来客人做什么好", []),
    ("随便做点什么", []),
    ("孩子不爱吃饭怎么办", []),
]

# contract 模式里用来验证「编出来的名字进不来」的假名字
FABRICATED = ["红烧肉", "五花肉", "松花蛋"]


class ScriptedCompletions:
    """把预设回答按顺序吐出来的假 client。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = types.SimpleNamespace(content=self.reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def scripted_client(reply: str):
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=ScriptedCompletions(reply)))


def payload_from(expected, with_fabricated: bool = False):
    """把标注拼成一个「模型回答」。with_fabricated 时掺进图上没有的名字。"""
    bucket = {R: [], I: [], T: []}
    for label, name in expected:
        bucket[label].append(name)
    if with_fabricated:
        bucket[R].extend(FABRICATED)
    return json.dumps(
        {"recipes": bucket[R], "ingredients": bucket[I], "topics": bucket[T]},
        ensure_ascii=False,
    )


def compare(result, expected):
    """比对 (label, 规范名) 集合，返回 (是否全对, 多选的, 漏选的)。"""
    got = {(m.label, m.name) for m in result.entities + result.topics}
    want = set(expected)
    return got == want, sorted(got - want), sorted(want - got)


def coverage_errors(vocab):
    """标注里写的名字必须真的在名单里，否则是标注写错了，不是模型不行。"""
    problems = []
    for query, expected in CASES:
        for label, name in expected:
            if vocab.entry_for(name, label) is None:
                problems.append(f"{query!r}: 名单里没有 {label}:{name}")
    return problems


def run_contract(vocab, extractor_cls, config):
    cases, dropped_ok, leaked = [], 0, []

    for query, expected in CASES:
        # ① 完美模型：期望名字原样返回 → 必须完全还原
        perfect = extractor_cls(scripted_client(payload_from(expected)), config, vocab)
        res_perfect = perfect.extract(query)
        ok, extra, missing = compare(res_perfect, expected)

        # ② 会编的模型：额外塞图上没有的名字 → 必须被 dropped，且结果不变
        noisy = extractor_cls(scripted_client(payload_from(expected, with_fabricated=True)), config, vocab)
        res_noisy = noisy.extract(query)
        ok_noisy, extra_noisy, missing_noisy = compare(res_noisy, expected)
        if res_noisy.dropped:
            dropped_ok += 1
        if not ok_noisy or extra_noisy:
            leaked.append({"query": query, "extra": extra_noisy, "dropped": res_noisy.dropped})

        cases.append(
            {
                "query": query,
                "expected": [f"{label}:{name}" for label, name in expected],
                "got": sorted(f"{m.label}:{m.name}" for m in res_perfect.entities + res_perfect.topics),
                "ok": ok,
                "extra": [f"{l}:{n}" for l, n in extra],
                "missing": [f"{l}:{n}" for l, n in missing],
                "droppedWithNoise": res_noisy.dropped,
                "elapsed_ms": round(res_perfect.elapsed_ms, 3),
            }
        )

    passed = sum(1 for c in cases if c["ok"] and not c["extra"])
    payload_cases = sum(1 for _q, e in CASES if e)
    return {
        "mode": "contract",
        "cases": cases,
        "passed": passed,
        "total": len(CASES),
        "accuracy": f"{passed}/{len(CASES)}",
        "casesWithExpectedEntities": payload_cases,
        "fabricatedNamesDropped": dropped_ok,
        "leaked": leaked,
        "note": "contract 模式不衡量模型质量，只验证「名单外的名字无法进入结果」",
    }


def run_llm(vocab, extractor_cls, config, timeout_note=""):
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("缺少 openai 包：pip install openai")

    api_key = os.environ.get("MODELSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL") or "https://api-inference.modelscope.cn/v1"
    if not api_key:
        sys.exit(
            "没有 API Key。请先 export MODELSCOPE_API_KEY=... 再跑；"
            "只想验证管线约束用默认的 contract 模式。"
        )

    client = OpenAI(api_key=api_key, base_url=base_url)
    extractor = extractor_cls(client, config, vocab)

    cases = []
    for query, expected in CASES:
        res = extractor.extract(query)
        ok, extra, missing = compare(res, expected)
        cases.append(
            {
                "query": query,
                "expected": [f"{label}:{name}" for label, name in expected],
                "got": sorted(f"{m.label}:{m.name}" for m in res.entities + res.topics),
                "ok": ok,
                "extra": [f"{l}:{n}" for l, n in extra],
                "missing": [f"{l}:{n}" for l, n in missing],
                "dropped": res.dropped,
                "attempts": res.attempts,
                "elapsed_ms": round(res.elapsed_ms, 1),
            }
        )

    passed = sum(1 for c in cases if c["ok"])
    return {
        "mode": "llm_vocab",
        "model": config.llm_model,
        "cases": cases,
        "passed": passed,
        "total": len(CASES),
        "accuracy": f"{passed}/{len(CASES)}",
        "mean_latency_ms": round(sum(c["elapsed_ms"] for c in cases) / max(len(cases), 1), 1),
        "note": timeout_note,
    }


def main():
    parser = argparse.ArgumentParser(description="关键词抽取评测")
    parser.add_argument(
        "--source",
        default="contract",
        choices=("contract", "llm_vocab"),
        help="contract=离线验证管线约束；llm_vocab=真调模型量准确率",
    )
    parser.add_argument("--nodes", default=str(NODES_CSV), help="nodes.csv 路径")
    parser.add_argument("--out", default=str(OUT_JSON), help="结果 JSON 输出路径")
    args = parser.parse_args()

    if not pathlib.Path(args.nodes).exists():
        sys.exit(f"找不到 {args.nodes}，先跑 scripts/rebuild_graph.py")

    vocab = gv.GraphVocabulary.from_nodes_csv(args.nodes)
    problems = coverage_errors(vocab)
    if problems:
        print("标注与名单不一致（先修标注，别拿它当模型指标）：")
        for item in problems:
            print("  -", item)
        sys.exit(2)

    config = types.SimpleNamespace(llm_model=os.environ.get("LLM_MODEL", "Qwen/Qwen3-8B"))

    if args.source == "contract":
        report = run_contract(vocab, lx.LLMEntityExtractor, config)
    else:
        report = run_llm(vocab, lx.LLMEntityExtractor, config)

    extractor = lx.LLMEntityExtractor(scripted_client("{}"), config, vocab)
    report.update(
        {
            "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "vocabulary": vocab.stats,
            "promptChars": len(extractor.prompt_head),
            "promptCharsPerCase": len(extractor.prompt_head),
            "annotatedQueries": len(CASES),
        }
    )

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print(f"名单：{vocab.stats}")
    print(f"提示词静态前缀：{report['promptChars']} 字符")
    print(f"模式 {report['mode']}：{report['accuracy']}")
    for case in report["cases"]:
        if not case["ok"] or case["extra"]:
            print(f"  ✗ {case['query']} 多选={case['extra']} 漏选={case['missing']}")
    if report["mode"] == "contract":
        names_dropped = sum(len(c.get("droppedWithNoise") or []) for c in report["cases"])
        print(
            f"  掺假名后的 {report['fabricatedNamesDropped']} 条问句里，"
            f"共 {names_dropped} 个图外名字被丢弃"
        )
        if report["leaked"]:
            print(f"  ✗ 有名字漏进结果：{report['leaked']}")
    print(f"写入 {out}")


if __name__ == "__main__":
    main()
