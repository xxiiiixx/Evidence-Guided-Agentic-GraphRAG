"""llm_entity_extractor 的单元测试（不联网、不调真模型）。

用一个假 client：这里要验的是**提示词与校验逻辑**，不是模型质量 ——
模型质量只能在真实词表上量（`scripts/eval_keyword_source.py --source llm_vocab`）。

跑法：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_package_modules():
    """把 rag_modules 当作合成包加载。

    `rag_modules/__init__.py` 会 import neo4j，本机没装，不能正常 import 包；
    而 llm_entity_extractor 用了 `from .graph_vocabulary import ...` 相对导入，
    单独按路径加载会失败。造一个只含 __path__ 的空包，把两个模块注册成它的
    子模块，相对导入就能解析。
    """
    pkg_name = "ragmods_extract_under_test"
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


gv, lx = _load_package_modules()


class Node:
    def __init__(self, node_id, name, **properties):
        self.node_id = node_id
        self.name = name
        self.properties = properties


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class FakeClient:
    def __init__(self, replies):
        self.chat = types.SimpleNamespace(completions=FakeCompletions(replies))


class FakeConfig:
    llm_model = "fake-model"


def build_vocabulary():
    recipes = [
        Node("201000001", "咖喱炒蟹", recipeCategory="水产", cuisineType="粤菜", difficulty="3.0"),
        Node("201000002", "红烧冬瓜", recipeCategory="素菜", difficulty="2.0"),
        # 跨 label 同名：「牛排」既是菜谱也是食材，名单里必须各占一条
        Node("201000003", "牛排", recipeCategory="荤菜", difficulty="2.0"),
    ]
    ingredients = [
        Node("201000100", "姜", synonyms='[{"term": "生姜"}]', mergedAliases="老姜"),
        Node("201000101", "蟹", synonyms='[{"term": "螃蟹"}]'),
        Node("201000102", "盐"),
        Node("201002876", "牛排"),
        Node("201000103", "牛肉"),
    ]
    return gv.GraphVocabulary().build(recipes, ingredients)


VOCAB = build_vocabulary()


def make_extractor(replies, max_per_type=8):
    client = FakeClient(replies)
    extractor = lx.LLMEntityExtractor(client, FakeConfig(), VOCAB, max_per_type=max_per_type)
    return extractor, client


class PromptTest(unittest.TestCase):
    def test_head_lists_counts_and_names(self):
        extractor, _ = make_extractor(['{"recipes": [], "ingredients": [], "topics": []}'])
        head = extractor.prompt_head
        self.assertIn("【菜谱名单｜3 个】", head)
        self.assertIn("【食材名单｜5 个】", head)
        self.assertIn("咖喱炒蟹", head)
        self.assertIn("红烧冬瓜", head)

    def test_head_lists_plain_names_without_alias_parentheses(self):
        """名单尽量简单：只印规范名，不印括号别名。"""
        extractor, _ = make_extractor(['{"recipes": [], "ingredients": [], "topics": []}'])
        head = extractor.prompt_head
        self.assertIn("姜", head)
        self.assertIn("蟹", head)
        self.assertNotIn("（生姜", head)
        self.assertNotIn("（螃蟹", head)

    def test_question_goes_last_for_prefix_cache(self):
        extractor, client = make_extractor(['{"recipes": [], "ingredients": []}'])
        extractor.extract("咖喱炒蟹怎么做？")
        prompt = client.chat.completions.calls[0]["messages"][0]["content"]
        self.assertTrue(prompt.endswith("咖喱炒蟹怎么做？"))
        self.assertTrue(prompt.startswith("你是实体链接器"))

    def test_temperature_is_zero_and_budget_is_small(self):
        extractor, client = make_extractor(['{"recipes": [], "ingredients": []}'])
        extractor.extract("随便")
        call = client.chat.completions.calls[0]
        self.assertEqual(call["temperature"], 0.0)
        self.assertLessEqual(call["max_tokens"], 512)
        self.assertEqual(call["model"], "fake-model")


class SelectionTest(unittest.TestCase):
    def test_in_vocabulary_names_become_matches_with_node_ids(self):
        extractor, _ = make_extractor(['{"recipes": ["咖喱炒蟹"], "ingredients": ["蟹", "姜"]}'])
        result = extractor.extract("咖喱炒蟹怎么做？")
        self.assertEqual([m.name for m in result.entities], ["咖喱炒蟹", "蟹", "姜"])
        self.assertEqual([m.label for m in result.entities], [gv.RECIPE, gv.INGREDIENT, gv.INGREDIENT])
        self.assertIn("201000001", result.node_ids)
        self.assertNotIn("201000102", result.node_ids)
        self.assertEqual(result.dropped, [])

    def test_out_of_vocabulary_names_are_dropped_not_fuzzy_matched(self):
        """「红烧肉」图上没有 —— 必须丢弃，不能退化成按名字模糊匹配。"""
        extractor, _ = make_extractor(
            ['{"recipes": ["红烧肉", "红烧冬瓜"], "ingredients": ["五花肉"]}']
        )
        result = extractor.extract("红烧肉怎么做？")
        self.assertEqual([m.name for m in result.entities], ["红烧冬瓜"])
        self.assertIn("Recipe:红烧肉", result.dropped)
        self.assertIn("Ingredient:五花肉", result.dropped)

    def test_alias_echo_is_resolved_to_canonical_name(self):
        """模型照抄别名「生姜」也应换回「姜」这条，而不是当作名单外的名字丢掉。"""
        extractor, _ = make_extractor(['{"ingredients": ["生姜"]}'])
        result = extractor.extract("生姜放多少？")
        self.assertEqual([m.name for m in result.entities], ["姜"])
        self.assertEqual(result.entities[0].node_id, "201000100")
        self.assertEqual(result.entities[0].surface, "生姜")
        self.assertEqual(result.dropped, [])

    def test_same_name_in_two_labels_keeps_the_right_node(self):
        extractor, _ = make_extractor(['{"recipes": ["牛排"], "ingredients": ["牛排"]}'])
        result = extractor.extract("牛排怎么做")
        by_label = {m.label: m.node_id for m in result.entities}
        self.assertEqual(by_label[gv.RECIPE], "201000003")
        self.assertEqual(by_label[gv.INGREDIENT], "201002876")

    def test_topics_are_kept_separate_from_entities(self):
        extractor, _ = make_extractor(['{"recipes": [], "ingredients": [], "topics": ["水产", "三星"]}'])
        result = extractor.extract("有没有简单的海鲜")
        self.assertEqual(result.topic_names, ["水产", "三星"])
        self.assertEqual(result.entities, [])

    def test_max_per_type_caps_each_list(self):
        extractor, _ = make_extractor(
            ['{"ingredients": ["蟹", "姜", "盐", "牛肉", "牛排"]}'], max_per_type=2
        )
        result = extractor.extract("q")
        self.assertEqual(len(result.ingredient_matches), 2)

    def test_duplicates_are_collapsed(self):
        extractor, _ = make_extractor(['{"ingredients": ["姜", "姜", "生姜"]}'])
        result = extractor.extract("姜")
        self.assertEqual([m.name for m in result.entities], ["姜"])


class RobustnessTest(unittest.TestCase):
    def test_code_fence_is_stripped(self):
        extractor, _ = make_extractor(['```json\n{"recipes": ["红烧冬瓜"]}\n```'])
        result = extractor.extract("红烧冬瓜")
        self.assertEqual([m.name for m in result.entities], ["红烧冬瓜"])
        self.assertEqual(result.attempts, 1)

    def test_bad_json_is_retried_then_fails_open(self):
        extractor, client = make_extractor(["这不是 JSON", "还是不是"])
        result = extractor.extract("咖喱炒蟹")
        self.assertEqual(result.entities, [])
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(client.chat.completions.calls), 2)
        self.assertEqual(result.attempts, result.attempts)

    def test_retry_succeeds_on_second_attempt(self):
        extractor, client = make_extractor(["oops", '{"recipes": ["咖喱炒蟹"]}'])
        result = extractor.extract("咖喱炒蟹")
        self.assertEqual([m.name for m in result.entities], ["咖喱炒蟹"])
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(client.chat.completions.calls), 2)

    def test_old_prompt_key_names_are_rejected(self):
        """模型退回旧提示词的键名（entity_keywords）时不能默默接受。"""
        extractor, _ = make_extractor(['{"entity_keywords": ["咖喱炒蟹"]}'])
        result = extractor.extract("咖喱炒蟹")
        self.assertEqual(result.entities, [])
        self.assertEqual(result.attempts, 2)

    def test_empty_query_does_not_call_the_model(self):
        extractor, client = make_extractor(['{"recipes": []}'])
        result = extractor.extract("   ")
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(result.entities, [])
        self.assertEqual(result.attempts, 0)

    def test_non_string_items_are_dropped_with_reason(self):
        extractor, _ = make_extractor(['{"ingredients": ["姜", 42]}'])
        result = extractor.extract("姜")
        self.assertEqual([m.name for m in result.entities], ["姜"])
        self.assertTrue(any("int" in d for d in result.dropped))


class MetadataTest(unittest.TestCase):
    def test_to_metadata_records_source_and_counts(self):
        extractor, _ = make_extractor(['{"recipes": ["咖喱炒蟹"]}'])
        meta = extractor.extract("咖喱炒蟹怎么做").to_metadata()
        self.assertEqual(meta["keyword_source"], "llm_vocab")
        self.assertEqual(meta["link_vocabulary"]["recipes"], 3)
        self.assertEqual(meta["matched_entities"][0]["name"], "咖喱炒蟹")
        self.assertEqual(meta["matched_entities"][0]["node_id"], "201000001")
        self.assertGreater(meta["prompt_chars"], 100)

    def test_summary_is_human_readable(self):
        extractor, _ = make_extractor(['{"recipes": ["咖喱炒蟹"], "topics": ["水产"]}'])
        summary = extractor.extract("咖喱炒蟹").summary()
        self.assertIn("咖喱炒蟹(Recipe)", summary)
        self.assertIn("水产", summary)


if __name__ == "__main__":
    unittest.main()
