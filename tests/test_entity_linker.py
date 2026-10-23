"""entity_linker 的单元测试。

为什么用 importlib 按文件路径加载：`rag_modules/__init__.py` 会 import neo4j，
本机（和 CI 的纯 Python 环境）不装 neo4j 时 `import rag_modules.entity_linker`
直接失败。entity_linker 本身零第三方依赖，按路径加载即可。

跑法：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_entity_linker():
    path = ROOT / "rag_modules" / "entity_linker.py"
    spec = importlib.util.spec_from_file_location("entity_linker_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


el = _load_entity_linker()


class Node:
    """替身：和 data_module.GraphNode 一样有 node_id / name / properties。"""

    def __init__(self, node_id, name, **properties):
        self.node_id = node_id
        self.name = name
        self.properties = properties


def build_tiny_linker():
    recipes = [
        Node("201000001", "番茄炒蛋", recipeCategory="荤菜", cuisineType="川菜"),
        # 与食材同名的菜谱，用来看类型消歧
        Node("201002876", "牛排", recipeCategory="荤菜"),
    ]
    ingredients = [
        Node("201000101", "番茄", synonyms='[{"term": "西红柿"}]'),
        Node("201000102", "鸡蛋"),
        Node("201000103", "八角", synonyms='[{"term": "大料"}]'),
        Node("201000104", "生抽", synonyms='[{"term": "酱油"}]'),
        # Day 3 同义词归一的产物：规范名是单字「姜」，原名记在 mergedAliases
        Node("201000106", "姜", mergedAliases="生姜"),
        Node("201002877", "牛排"),
        # 单字名，按设计不进词表
        Node("201000105", "盐"),
    ]
    return el.EntityLinker().build(recipes, ingredients)


class TestAhoCorasick(unittest.TestCase):
    def test_longest_at_same_start(self):
        ac = el.AhoCorasick()
        for p in ("他", "他们", "她们"):
            ac.add(p)
        ac.build()
        hits = ac.find("他们说")
        self.assertIn((0, 2, "他们"), hits)

    def test_multiple_positions(self):
        ac = el.AhoCorasick()
        for p in ("大蒜", "生抽"):
            ac.add(p)
        ac.build()
        hits = ac.find("大蒜和生抽")
        self.assertEqual({h[2] for h in hits}, {"大蒜", "生抽"})

    def test_no_hit(self):
        ac = el.AhoCorasick()
        ac.add("八角")
        ac.build()
        self.assertEqual(ac.find("今天天气不错"), [])

    def test_find_auto_builds(self):
        # find() 内部会补一次 build()，省掉调用方漏 build 的坑
        ac = el.AhoCorasick()
        ac.add("八角")
        self.assertEqual(ac.find("八角"), [(0, 2, "八角")])


class TestParseAliases(unittest.TestCase):
    def test_json_list(self):
        self.assertEqual(el.parse_aliases('[{"term": "大料"}]'), ["大料"])

    def test_plain_list(self):
        self.assertEqual(el.parse_aliases(["大料", "八角"]), ["大料", "八角"])

    def test_empty_values(self):
        for raw in (None, "", "   ", "[]", [], "null"):
            self.assertEqual(el.parse_aliases(raw), [], raw)


class TestLinkerEntity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linker = build_tiny_linker()

    def test_recipe_by_exact_name(self):
        res = self.linker.link("番茄炒蛋怎么做")
        names = [(m.name, m.label) for m in res.entities]
        self.assertEqual(names, [("番茄炒蛋", el.RECIPE)])

    def test_alias_maps_to_canonical_name(self):
        res = self.linker.link("西红柿要不要去皮")
        self.assertEqual([m.name for m in res.ingredient_matches], ["番茄"])
        self.assertEqual(res.ingredient_matches[0].entry.source, "alias")

    def test_alias_not_leaking_as_own_entity(self):
        res = self.linker.link("西红柿要不要去皮")
        self.assertNotIn("西红柿", res.entity_names)

    def test_longest_match_wins(self):
        res = self.linker.link("番茄炒蛋")
        self.assertEqual([m.surface for m in res.entities], ["番茄炒蛋"])

    def test_spans_do_not_overlap(self):
        res = self.linker.link("番茄炒蛋配西红柿")
        spans = [(m.start, m.end) for m in res.entities]
        self.assertEqual(spans, sorted(spans))
        for prev, cur in zip(spans, spans[1:]):
            self.assertLessEqual(prev[1], cur[0], spans)

    def test_single_char_ingredient_not_indexed(self):
        res = self.linker.link("盐放多少")
        self.assertEqual(res.entities, [])

    def test_merged_alias_maps_to_canonical(self):
        # Day 3 把「生姜」并进了「姜」，原名只留在 mergedAliases 列；
        # 链接器必须读这一列，否则同义词归一等于没做。
        res = self.linker.link("生姜要切片吗")
        self.assertEqual([m.name for m in res.ingredient_matches], ["姜"])
        self.assertEqual(res.ingredient_matches[0].entry.source, "merged")

    def test_no_match(self):
        res = self.linker.link("今天天气如何")
        self.assertEqual(res.entities, [])
        self.assertEqual(res.topics, [])

    def test_empty_query(self):
        res = self.linker.link("   ")
        self.assertEqual(res.entities, [])

    def test_multi_entity(self):
        res = self.linker.link("番茄和鸡蛋一起炒")
        self.assertEqual(sorted(res.entity_names), ["番茄", "鸡蛋"])


class TestDisambiguation(unittest.TestCase):
    """同名跨 label（牛排 既是菜谱又是食材）。"""

    @classmethod
    def setUpClass(cls):
        cls.linker = build_tiny_linker()

    def test_default_prefers_recipe_and_flags_ambiguous(self):
        res = self.linker.link("牛排")
        self.assertEqual([m.label for m in res.entities], [el.RECIPE])
        self.assertTrue(res.entities[0].ambiguous)
        self.assertEqual(res.entities[0].reason, "default")

    def test_recipe_hint(self):
        res = self.linker.link("牛排怎么做")
        self.assertEqual([m.label for m in res.entities], [el.RECIPE])
        self.assertFalse(res.entities[0].ambiguous)
        self.assertEqual(res.entities[0].reason, "recipe_hint")

    def test_ingredient_hint(self):
        res = self.linker.link("牛排买哪个部位")
        self.assertEqual([m.label for m in res.entities], [el.INGREDIENT])
        self.assertFalse(res.entities[0].ambiguous)
        self.assertEqual(res.entities[0].reason, "ingredient_hint")

    def test_ambiguous_matches_exposed(self):
        res = self.linker.link("牛排")
        self.assertEqual([m.surface for m in res.ambiguous_matches], ["牛排"])


class TestAliasNamePreference(unittest.TestCase):
    """真实数据里 7 组「某食材的别名 = 另一食材的正名」。"""

    @classmethod
    def setUpClass(cls):
        cls.linker = el.EntityLinker().build(
            [],
            [
                Node("201000201", "大料"),
                Node("201000202", "八角", synonyms='[{"term": "大料"}]'),
            ],
        )

    def test_prefers_exact_name_over_alias(self):
        res = self.linker.link("大料放多少")
        self.assertEqual([m.name for m in res.ingredient_matches], ["大料"])

    def test_canonical_name_still_resolves(self):
        res = self.linker.link("八角在哪买")
        self.assertEqual([m.name for m in res.ingredient_matches], ["八角"])


class TestTopics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.linker = build_tiny_linker()

    def test_recipe_category_is_topic(self):
        res = self.linker.link("荤菜有哪些")
        self.assertIn("荤菜", res.topic_names)

    def test_topic_alias(self):
        res = self.linker.link("川菜有什么特色")
        self.assertIn("川菜", res.topic_names)

    def test_topic_not_in_entities(self):
        res = self.linker.link("荤菜有哪些")
        self.assertNotIn("荤菜", res.entity_names)


class TestStats(unittest.TestCase):
    def test_counts(self):
        linker = build_tiny_linker()
        stats = linker.stats
        self.assertEqual(stats["recipes"], 2)
        self.assertEqual(stats["ingredients"], 7)
        # 牛排 一个 surface 对应菜谱 + 食材两个 entry
        self.assertEqual(stats["collisions"], 1)
        self.assertGreaterEqual(stats["skippedShortSurface"], 1)  # 盐

    def test_vocabulary_size_counts_surfaces(self):
        linker = build_tiny_linker()
        self.assertEqual(linker.vocabulary_size, len(linker._entries))


class TestRealGraphVocabulary(unittest.TestCase):
    """用 Day 3 的重建产物做一次真实规模的校验。"""

    CSV = ROOT / "data" / "rebuild" / "nodes.csv"

    @classmethod
    def setUpClass(cls):
        if not cls.CSV.exists():
            raise unittest.SkipTest(f"缺少重建产物: {cls.CSV}")
        cls.linker = el.EntityLinker.from_nodes_csv(str(cls.CSV))

    def test_node_counts_match_day3(self):
        stats = self.linker.stats
        self.assertEqual(stats["recipes"], 321)
        self.assertEqual(stats["ingredients"], 746)

    def test_cross_label_collisions_match_day3(self):
        # Day 3 实测 11 组菜谱/食材同名（牛排、小龙虾、凉皮…）。
        # stats["collisions"] 算的是「任何 surface 对应多个 entry」，会把
        # cuisineType 和 recipeCategory 撞名的主題词也算进去，所以这里只挑
        # 真正横跨 Recipe/Ingredient 两个 label 的。
        mixed = [
            surface
            for surface, bucket in self.linker._entries.items()
            if {e.label for e in bucket} >= {el.RECIPE, el.INGREDIENT}
        ]
        self.assertEqual(len(mixed), 11)

    def test_curry_crab(self):
        res = self.linker.link("咖喱炒蟹怎么做")
        self.assertEqual([m.name for m in res.recipe_matches], ["咖喱炒蟹"])

    def test_link_is_fast(self):
        res = self.linker.link("咖喱炒蟹怎么做？需要哪些食材，用多少生抽")
        self.assertLess(res.elapsed_ms, 50.0)


if __name__ == "__main__":
    unittest.main()
