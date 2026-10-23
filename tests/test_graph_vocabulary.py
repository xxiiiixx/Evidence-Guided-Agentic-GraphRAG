"""graph_vocabulary 的单元测试（不连 Neo4j、不联网）。

这个模块只负责「把图上的名字整理成名单」，所以测试集中在四件事：
别名有没有被读到、主题有没有从菜谱属性派生出来、名单里的名字能不能换回 nodeId、
以及跨 label 同名（菜谱「牛排」和食材「牛排」）是否各占一条而不是互相覆盖。

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
REAL_NODES = ROOT / "data" / "rebuild" / "nodes.csv"


def _load_vocabulary():
    """按文件路径加载模块。

    `rag_modules/__init__.py` 会 import neo4j，本机没装，所以不能正常 import 包；
    graph_vocabulary 本身零第三方依赖，造一个只含 __path__ 的空包按路径加载即可。
    """
    pkg_name = "ragmods_vocab_under_test"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(ROOT / "rag_modules")]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    full = f"{pkg_name}.graph_vocabulary"
    spec = importlib.util.spec_from_file_location(full, ROOT / "rag_modules" / "graph_vocabulary.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


gv = _load_vocabulary()


class Node:
    def __init__(self, node_id, name, **properties):
        self.node_id = node_id
        self.name = name
        self.properties = properties


def recipe(node_id, name, **props):
    props.setdefault("recipeCategory", "荤菜")
    return Node(node_id, name, **props)


def ingredient(node_id, name, **props):
    return Node(node_id, name, **props)


class ParseAliasesTest(unittest.TestCase):
    def test_term_dicts(self):
        self.assertEqual(gv.parse_aliases('[{"term": "大料"}, {"term": "八角"}]'), ["大料", "八角"])

    def test_plain_list(self):
        self.assertEqual(gv.parse_aliases('["淡色酱油", "鲜味酱油"]'), ["淡色酱油", "鲜味酱油"])

    def test_separated_text(self):
        self.assertEqual(gv.parse_aliases("黄酒、绍兴酒"), ["黄酒", "绍兴酒"])

    def test_empty_markers(self):
        for raw in (None, "", "null", "None", "[]"):
            self.assertEqual(gv.parse_aliases(raw), [], msg=repr(raw))

    def test_merged_aliases_is_plain_text(self):
        # Day 3 同义词归一的产物是普通文本，不是 JSON —— 按 JSON 解会抛异常
        self.assertEqual(gv.parse_merged_aliases("生姜"), ["生姜"])
        self.assertEqual(gv.parse_merged_aliases(""), [])


class BuildTest(unittest.TestCase):
    def setUp(self):
        recipes = [
            recipe("201000001", "咖喱炒蟹", recipeCategory="水产", cuisineType="粤菜", difficulty="3.0"),
            recipe("201000002", "牛排", recipeCategory="荤菜,凉菜"),
        ]
        ingredients = [
            ingredient("201002876", "牛排"),
            ingredient("201000100", "姜", synonyms='[{"term": "生姜"}]', mergedAliases="老姜"),
            ingredient("201000101", "盐", synonyms='[{"term": "盐"}]'),
        ]
        self.vocab = gv.GraphVocabulary().build(recipes, ingredients)

    def test_counts_split_by_label(self):
        stats = self.vocab.stats
        self.assertEqual(stats["recipes"], 2)
        self.assertEqual(stats["ingredients"], 3)
        self.assertEqual(stats["recipeEntries"], 2)
        self.assertEqual(stats["ingredientEntries"], 3)

    def test_topics_derived_from_recipe_properties(self):
        topics = self.vocab.names_by_label()[gv.TOPIC]
        for name in ("水产", "荤菜", "凉菜", "粤菜", "三星"):
            self.assertIn(name, topics)
        self.assertEqual(self.vocab.entry_for("三星", gv.TOPIC).source, "difficulty")

    def test_display_is_plain_name_without_aliases(self):
        """提示词里只印规范名：别名留在索引里换回规范名，不占 token。"""
        displays = self.vocab.display_by_label()[gv.INGREDIENT]
        self.assertIn("姜", displays)
        self.assertFalse(any("（" in d for d in displays))
        # 但别名依然能换回规范名
        self.assertEqual(self.vocab.entry_for("生姜", gv.INGREDIENT).name, "姜")

    def test_entry_for_accepts_alias(self):
        self.assertEqual(self.vocab.entry_for("生姜", gv.INGREDIENT).name, "姜")
        self.assertEqual(self.vocab.entry_for("老姜", gv.INGREDIENT).node_id, "201000100")

    def test_same_name_different_label_stays_separate(self):
        """跨 label 同名必须各占一条，否则菜谱的 nodeId 会把食材那条盖掉。"""
        self.assertEqual(self.vocab.entry_for("牛排", gv.RECIPE).node_id, "201000002")
        self.assertEqual(self.vocab.entry_for("牛排", gv.INGREDIENT).node_id, "201002876")

    def test_unknown_name_returns_none(self):
        self.assertIsNone(self.vocab.entry_for("红烧肉", gv.RECIPE))
        self.assertIsNone(self.vocab.entry_for("", gv.RECIPE))
        self.assertIsNone(self.vocab.entry_for("牛排", gv.TOPIC))

    def test_alias_index_does_not_leak_across_labels(self):
        """「水产」是菜谱分类，不该被当食材别名返回。"""
        self.assertIsNone(self.vocab.entry_for("水产", gv.INGREDIENT))


class DifficultyTierTest(unittest.TestCase):
    def test_rounds_to_nearest_tier(self):
        self.assertEqual(gv._difficulty_tier("1.0"), "一星")
        self.assertEqual(gv._difficulty_tier(4.4), "四星")
        self.assertEqual(gv._difficulty_tier(4.6), "五星")

    def test_missing_or_dirty_returns_none(self):
        for value in (None, "", "null", "难", 0, 9):
            self.assertIsNone(gv._difficulty_tier(value), msg=repr(value))


class FromCsvTest(unittest.TestCase):
    def test_reads_labels_and_properties(self):
        rows = [
            {"nodeId": "1", "labels": "Recipe", "name": "番茄炒蛋", "recipeCategory": "素菜",
             "difficulty": "2.0", "synonyms": "", "mergedAliases": ""},
            {"nodeId": "2", "labels": "Ingredient", "name": "番茄", "synonyms": '[{"term": "西红柿"}]',
             "mergedAliases": ""},
            {"nodeId": "3", "labels": "CookingStep", "name": "切块"},
        ]
        # 走内存里的行，不落盘：沙箱里往临时目录写文件会 PermissionError
        vocab = gv.GraphVocabulary.from_nodes_rows(rows)
        self.assertEqual(vocab.stats["recipes"], 1)
        self.assertEqual(vocab.stats["ingredients"], 1)
        self.assertEqual(vocab.entry_for("西红柿", gv.INGREDIENT).name, "番茄")
        # CookingStep 不进名单：实体链接只认菜谱、食材、主题
        self.assertIsNone(vocab.entry_for("切块", gv.INGREDIENT))

    @unittest.skipUnless(REAL_NODES.exists(), "需要先跑 scripts/rebuild_graph.py")
    def test_real_nodes_csv_has_expected_scale(self):
        vocab = gv.GraphVocabulary.from_nodes_csv(str(REAL_NODES))
        stats = vocab.stats
        self.assertEqual(stats["recipes"], 321)
        self.assertEqual(stats["ingredients"], 746)
        # 主题来自 recipeCategory + cuisineType + 难度档位
        self.assertGreater(stats["topicEntries"], 30)
        self.assertIsNotNone(vocab.entry_for("咖喱炒蟹", gv.RECIPE))


class MergeTest(unittest.TestCase):
    def test_same_key_twice_merges_aliases_and_keeps_node_id(self):
        vocab = gv.GraphVocabulary()
        vocab._add("生抽", gv.INGREDIENT, "n1", ["酱油"], "name")
        vocab._add("生抽", gv.INGREDIENT, None, ["淡酱油"], "name")
        entry = vocab.entry_for("生抽", gv.INGREDIENT)
        self.assertEqual(entry.node_id, "n1")
        self.assertEqual(entry.aliases, ("酱油", "淡酱油"))


if __name__ == "__main__":
    unittest.main()
