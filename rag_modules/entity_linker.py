"""
实体链接：在图谱词表上做确定性字符串匹配

Day 4 用这个模块替换 `extract_query_keywords()` 里那次 LLM 调用。理由三条：

1. **LLM 在猜名字。** 旧提示词让模型"推测相关的具体食材/菜品"，举例是鸡胸肉、
   西兰花、平底锅。它不知道图谱里到底有哪些节点，而下游 `get_entities_by_key()`
   是按名字精确查字典——猜的名字图谱里没有就整条落空，猜错了还会命中另一个实体。
   图谱里只有 1104 个候选名字，本来不需要猜。
2. **它在关键路径上。** perf/timing 分支的分段计时（docs/day02.md §8.3）显示
   `dual_level_retrieval` 14808.8 ms 里有 14799 ms 是这一次调用，占检索耗时 88.4%。
3. **query 很短。** 实测样本都在 30 字以内，词表 1104 个模式，匹配是微秒级的事。

算法用 Aho-Corasick，纯 Python 实现（failure link + 输出链），不引第三方依赖。1104 个
模式在 30 字文本上的开销可以忽略，换成 C 扩展省下的那点时间去抵服务器装 wheel /
编译扩展的成本不划算。

匹配策略是「最左最长、不重叠」：从左往右扫，同一位置取最长的词表条目，命中后从它的
末尾继续。这样「红烧肉」不会被拆成「红烧」+「肉」，「咖喱炒蟹」也不会因为词表里有
「咖喱块」而被截断。

已知取舍：**实体名长度 1 的不进词表。** 重建图里有 11 个单字食材名（冰 姜 水 盐 米 肉
葱 虾 醋 面 鸡），但「水煮鱼」里的水、「盐水鸭」里的盐都会被误命中，而单字命中的
信息量极低。代价是「盐放多少」这类问句匹配不到实体，此时检索退回向量 + BM25。
主题词不受这条限制（「汤」有用，且总会被更长的菜名压住）。
"""

from __future__ import annotations

import ast
import csv
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 标签常量。Topic 不是图里的节点，是 recipeCategory / cuisineType / 难度档位的总称，
# 只用来喂 topic_level_retrieval。
RECIPE = "Recipe"
INGREDIENT = "Ingredient"
TOPIC = "Topic"

# 同一个 surface 落到多个 entry 时的优先级（越小越优先）：正名 > 别名 > 合并进来的原名。
# 真实数据里有 7 组「某食材的别名 = 另一食材的正名」，不能靠入表先后决定。
_SOURCE_RANK: Dict[str, int] = {
    "name": 0,
    "alias": 1,
    "merged": 2,
    "category": 3,
    "cuisine": 4,
    "difficulty": 5,
    "topic_alias": 6,
}

# 实体名的最短长度，见模块 docstring 的"已知取舍"
_MIN_ENTITY_SURFACE_LEN = 2

# 倾向词：判断跨 label 同名（菜谱 vs 食材）到底指哪个。
# 菜谱倾向词优先——「做法/步骤/教程」几乎只出现在问菜谱的句子里。
_RECIPE_HINTS: Tuple[str, ...] = (
    "怎么做", "做法", "怎么烧", "怎么炒", "怎么炖", "怎么蒸", "怎么煮", "怎么拌",
    "怎么煎", "怎么烤", "菜谱", "教程", "步骤", "如何做", "怎么吃", "介绍一下", "好吃",
)
_INGREDIENT_HINTS: Tuple[str, ...] = (
    "买", "价格", "多少钱", "替代", "代替", "换成", "配料", "含有",
    "热量", "营养", "保存", "存放", "挑选", "怎么选", "一斤", "多少克", "是什么",
)
# 倾向词只在命中位置左右这么多字符内找，避免一句话里两个同名实体互相干扰
_HINT_WINDOW = 10

# 难度档位。图里 DifficultyLevel 节点名是一星…五星，Recipe.difficulty 是 1.0–5.0 的浮点
_DIFFICULTY_TOPICS: Tuple[str, ...] = ("一星", "二星", "三星", "四星", "五星")

# 主题别名表：图谱里的分类名/菜系名是书面语（减肥餐、素菜、汤类），问句里常是口语。
# 只收有把握的映射，宁可漏也不要把「小菜」这类多义词塞进来。
_TOPIC_ALIASES: Dict[str, Tuple[str, ...]] = {
    "素菜": ("素食", "全素", "吃素"),
    "荤菜": ("肉菜", "有肉"),
    "汤类": ("汤", "煲汤", "羹"),
    "主食": ("面食", "拌饭", "盖饭", "下饭"),
    "水产": ("海鲜", "鱼虾", "河鲜"),
    "早餐": ("早饭", "早点"),
    "饮料": ("饮品", "喝点"),
    "甜品": ("甜点", "糖水", "蛋糕"),
    "调料": ("酱料", "蘸料", "佐料"),
    "半成品": ("预制", "速冻"),
    "凉菜": ("凉拌", "冷盘"),
    "下酒菜": ("下酒", "配酒"),
    "减肥餐": ("减肥", "低脂", "低热量", "减脂", "瘦身", "轻食", "控卡"),
    "一星": ("简单", "容易", "新手", "入门", "零基础"),
    "三星": ("中等", "适中", "一般难度"),
    "五星": ("困难", "复杂", "挑战", "有难度"),
}

_CJK_RANGE = ("\u4e00", "\u9fff")


# ----------------------------------------------------------------------------------------
# 词表
# ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VocabularyEntry:
    """词表里的一个候选。同一个 surface 可能对应多个 entry（跨 label 同名）。"""

    surface: str
    name: str
    label: str
    node_id: Optional[str]
    source: str  # name / alias / category / cuisine / topic_alias / difficulty


@dataclass
class Match:
    """一次命中。"""

    entry: VocabularyEntry
    start: int
    end: int
    surface: str
    ambiguous: bool = False
    reason: str = ""  # recipe_hint / ingredient_hint / default / single

    @property
    def label(self) -> str:
        return self.entry.label

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def node_id(self) -> Optional[str]:
        return self.entry.node_id

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "surface": self.surface,
            "name": self.name,
            "label": self.label,
            "node_id": self.node_id,
            "source": self.entry.source,
            "start": self.start,
            "end": self.end,
            "ambiguous": self.ambiguous,
            "reason": self.reason,
        }


@dataclass
class LinkResult:
    """一次链接的完整结果。"""

    query: str
    entities: List[Match] = field(default_factory=list)
    topics: List[Match] = field(default_factory=list)
    elapsed_ms: float = 0.0
    vocabulary: Dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _dedupe(seq: Iterable[str]) -> List[str]:
        seen, out = set(), []
        for s in seq:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @property
    def recipe_matches(self) -> List[Match]:
        return [m for m in self.entities if m.label == RECIPE]

    @property
    def ingredient_matches(self) -> List[Match]:
        return [m for m in self.entities if m.label == INGREDIENT]

    @property
    def ambiguous_matches(self) -> List[Match]:
        return [m for m in self.entities + self.topics if m.ambiguous]

    @property
    def entity_names(self) -> List[str]:
        return self._dedupe(m.name for m in self.entities)

    @property
    def topic_names(self) -> List[str]:
        return self._dedupe(m.name for m in self.topics)

    @property
    def node_ids(self) -> List[str]:
        return self._dedupe(m.node_id for m in self.entities if m.node_id)

    def summary(self) -> str:
        ents = ", ".join(f"{m.surface}->{m.name}({m.label})" for m in self.entities) or "-"
        tops = ", ".join(self.topic_names) or "-"
        amb = ", ".join(m.surface for m in self.ambiguous_matches) or "-"
        return f"entities=[{ents}] topics=[{tops}] ambiguous=[{amb}] {self.elapsed_ms:.2f}ms"

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "keyword_source": "entity_linker",
            "link_elapsed_ms": round(self.elapsed_ms, 3),
            "link_vocabulary": self.vocabulary,
            "matched_entities": [m.to_metadata() for m in self.entities],
            "matched_topics": [m.to_metadata() for m in self.topics],
            "ambiguous_surfaces": [m.surface for m in self.ambiguous_matches],
        }


# ----------------------------------------------------------------------------------------
# Aho-Corasick
# ----------------------------------------------------------------------------------------


class AhoCorasick:
    """最简 Aho-Corasick：add 若干模式 → build 一次 → find 多次。

    复杂度 O(len(text) + 命中数)，与模式数量无关。
    """

    __slots__ = ("_goto", "_fail", "_out", "_built")

    def __init__(self) -> None:
        self._goto: List[Dict[str, int]] = [{}]
        self._fail: List[int] = [0]
        self._out: List[List[str]] = [[]]
        self._built = False

    def add(self, pattern: str) -> None:
        if not pattern:
            return
        node = 0
        for ch in pattern:
            nxt = self._goto[node].get(ch)
            if nxt is None:
                self._goto.append({})
                self._fail.append(0)
                self._out.append([])
                nxt = len(self._goto) - 1
                self._goto[node][ch] = nxt
            node = nxt
        self._out[node].append(pattern)

    def build(self) -> None:
        queue: deque = deque()
        # 根的直接子节点 fail 指向根
        for child in self._goto[0].values():
            self._fail[child] = 0
            queue.append(child)
        while queue:
            node = queue.popleft()
            for ch, child in self._goto[node].items():
                queue.append(child)
                f = self._fail[node]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                self._fail[child] = self._goto[f].get(ch, 0)
                # 输出链：把 fail 节点的输出接过来，否则会漏报后缀模式
                self._out[child].extend(self._out[self._fail[child]])
        self._built = True

    def find(self, text: str) -> List[Tuple[int, int, str]]:
        """返回 [(start, end, pattern)]，end 为开区间。允许重叠，由调用方筛选。"""
        if not self._built:
            self.build()
        hits: List[Tuple[int, int, str]] = []
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            if self._out[node]:
                end = i + 1
                for pattern in self._out[node]:
                    hits.append((end - len(pattern), end, pattern))
        return hits


# ----------------------------------------------------------------------------------------
# 属性解析小工具
# ----------------------------------------------------------------------------------------


def _is_cjk(s: str) -> bool:
    return all(_CJK_RANGE[0] <= c <= _CJK_RANGE[1] for c in s)


def _split_prop(value: Any) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def parse_aliases(raw: Any) -> List[str]:
    """解析 synonyms 属性。

    图里这个字段有两种写法：食材是合法的 JSON（[{"term": "大料"}]），
    菜谱里混着一批单引号的（[{'term': '油闷大虾'}]），所以先 json 再 ast。
    只保留纯中文、长度 2–8 的别名：菜谱里有一堆机器翻译垃圾
    （braised鱼、oil-braised大虾、清steamed生蚝），进词表只会制造误命中。
    """
    if not raw:
        return []
    data = None
    if isinstance(raw, (list, tuple)):
        data = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        for loader in (json.loads, ast.literal_eval):
            try:
                data = loader(text)
                break
            except Exception:
                continue
    if not isinstance(data, (list, tuple)):
        return []

    out: List[str] = []
    for item in data:
        term = item.get("term") if isinstance(item, dict) else item
        if not isinstance(term, str):
            continue
        term = term.strip()
        if 2 <= len(term) <= 8 and _is_cjk(term) and term not in out:
            out.append(term)
    return out


def parse_merged_aliases(raw: Any) -> List[str]:
    """解析 mergedAliases —— Day 3 合并同义食材时记下的原名。

    和 synonyms 不是一个格式：这里是纯文本、逗号分隔（`生姜`、`黄酒`），
    不是 JSON。漏读这一列等于把 Day 3 的 9 组同义词归一白做了：
    「生姜」查不到「姜」，「黄酒」查不到「料酒」。
    """
    if not raw:
        return []
    text = str(raw).replace("|", ",")
    out: List[str] = []
    for part in text.split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _category_tokens(properties: Dict[str, Any]) -> List[str]:
    """一个菜谱可能有多个分类（4 道菜），图里 recipeCategory 是逗号串。"""
    props = properties or {}
    cats = props.get("all_categories")
    if isinstance(cats, (list, tuple)) and cats:
        tokens = [str(c).strip() for c in cats]
    else:
        tokens = _split_prop(props.get("recipeCategory") or props.get("category"))
    return [c for c in tokens if c and c != "未知"]


# ----------------------------------------------------------------------------------------
# 链接器
# ----------------------------------------------------------------------------------------


class EntityLinker:
    """图谱词表 + Aho-Corasick + 类型消歧。

    用法：
        linker = EntityLinker.from_nodes_csv("data/rebuild/nodes.csv")   # 离线可用
        linker = EntityLinker().build(recipes, ingredients)              # 接 data_module
        result = linker.link("咖喱炒蟹怎么做")
    """

    def __init__(self) -> None:
        self._entries: Dict[str, List[VocabularyEntry]] = {}
        self._automaton = AhoCorasick()
        self._stats: Dict[str, int] = {}
        self._built = False

    # ---------------------------------------------------------------- 构建

    def _add(self, surface: Any, name: str, label: str, node_id: Optional[str], source: str) -> None:
        if not isinstance(surface, str):
            return
        surface = surface.strip()
        if not surface:
            return
        if label != TOPIC and len(surface) < _MIN_ENTITY_SURFACE_LEN:
            self._stats["skippedShortSurface"] = self._stats.get("skippedShortSurface", 0) + 1
            return
        bucket = self._entries.setdefault(surface, [])
        for e in bucket:
            if e.label == label and e.name == name and e.node_id == node_id:
                return
        bucket.append(VocabularyEntry(surface, name, label, node_id, source))

    def build(self, recipes: Iterable[Any], ingredients: Iterable[Any]) -> "EntityLinker":
        """从 data_module 的 GraphNode 列表建词表。"""
        n_recipes = n_ingredients = 0

        for r in recipes:
            props = getattr(r, "properties", None) or {}
            name = getattr(r, "name", "") or ""
            node_id = getattr(r, "node_id", None)
            if not name:
                continue
            n_recipes += 1
            self._add(name, name, RECIPE, node_id, "name")
            for alias in parse_aliases(props.get("synonyms")):
                self._add(alias, name, RECIPE, node_id, "alias")
            for alias in parse_merged_aliases(props.get("mergedAliases")):
                self._add(alias, name, RECIPE, node_id, "merged")
            for cat in _category_tokens(props):
                self._add(cat, cat, TOPIC, None, "category")
            for cui in _split_prop(props.get("cuisineType")):
                self._add(cui, cui, TOPIC, None, "cuisine")

        for i in ingredients:
            props = getattr(i, "properties", None) or {}
            name = getattr(i, "name", "") or ""
            node_id = getattr(i, "node_id", None)
            if not name:
                continue
            n_ingredients += 1
            self._add(name, name, INGREDIENT, node_id, "name")
            for alias in parse_aliases(props.get("synonyms")):
                self._add(alias, name, INGREDIENT, node_id, "alias")
            for alias in parse_merged_aliases(props.get("mergedAliases")):
                self._add(alias, name, INGREDIENT, node_id, "merged")

        for canonical, aliases in _TOPIC_ALIASES.items():
            for a in aliases:
                self._add(a, canonical, TOPIC, None, "topic_alias")
        for d in _DIFFICULTY_TOPICS:
            self._add(d, d, TOPIC, None, "difficulty")

        self._compile(n_recipes, n_ingredients)
        return self

    @classmethod
    def from_nodes_csv(cls, path: str) -> "EntityLinker":
        """不依赖 Neo4j 的构建路径：直接读图重建产出的 nodes.csv。

        仅供评测脚本和单测使用；线上走 build()，数据来自 data_module。
        """

        @dataclass
        class _Node:
            node_id: str
            name: str
            properties: Dict[str, Any]

        recipes: List[_Node] = []
        ingredients: List[_Node] = []
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                label = row.get("labels", "")
                if label not in (RECIPE, INGREDIENT):
                    continue
                props = {k: v for k, v in row.items() if v not in ("", None)}
                node = _Node(node_id=row.get("nodeId", ""), name=row.get("name", ""), properties=props)
                (recipes if label == RECIPE else ingredients).append(node)
        return cls().build(recipes, ingredients)

    def _compile(self, n_recipes: int, n_ingredients: int) -> None:
        for surface in self._entries:
            self._automaton.add(surface)
        self._automaton.build()
        self._built = True

        by_label: Dict[str, int] = {}
        for bucket in self._entries.values():
            for e in bucket:
                by_label[e.label] = by_label.get(e.label, 0) + 1
        self._stats.update(
            {
                "recipes": n_recipes,
                "ingredients": n_ingredients,
                "surfaces": len(self._entries),
                "entries": sum(len(b) for b in self._entries.values()),
                "recipeEntries": by_label.get(RECIPE, 0),
                "ingredientEntries": by_label.get(INGREDIENT, 0),
                "topicEntries": by_label.get(TOPIC, 0),
                # 一个 surface 映射到多个 entry，就是跨 label 同名（菜谱 vs 食材）
                "collisions": sum(1 for b in self._entries.values() if len(b) > 1),
            }
        )
        logger.info(f"实体链接词表构建完成: {self._stats}")

    # ---------------------------------------------------------------- 查询

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    @property
    def vocabulary_size(self) -> int:
        return len(self._entries)

    def link(self, query: str) -> LinkResult:
        """把 query 里的实体和主题词抽出来。"""
        started = time.perf_counter()
        if not self._built:
            raise RuntimeError("EntityLinker 尚未构建，先调用 build() 或 from_nodes_csv()")

        text = (query or "").strip()
        chosen = self._select(text, self._automaton.find(text)) if text else []

        result = LinkResult(query=text, elapsed_ms=0.0, vocabulary=dict(self._stats))
        seen_entity: set = set()
        seen_topic: set = set()
        for entry, start, end, ambiguous, reason in chosen:
            match = Match(entry=entry, start=start, end=end, surface=text[start:end],
                          ambiguous=ambiguous, reason=reason)
            if entry.label == TOPIC:
                if entry.name not in seen_topic:
                    seen_topic.add(entry.name)
                    result.topics.append(match)
            else:
                key = (entry.label, entry.node_id)
                if key not in seen_entity:
                    seen_entity.add(key)
                    result.entities.append(match)

        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        return result

    def _select(
        self, text: str, hits: Sequence[Tuple[int, int, str]]
    ) -> List[Tuple[VocabularyEntry, int, int, bool, str]]:
        """最左最长、不重叠。同一位置同长度有多个 entry 时走类型消歧。"""
        if not hits:
            return []
        # 位置升序；同起点取长的
        ordered = sorted(hits, key=lambda h: (h[0], -(h[1] - h[0])))
        out: List[Tuple[VocabularyEntry, int, int, bool, str]] = []
        cursor = 0
        for start, end, surface in ordered:
            if start < cursor:
                continue
            entries = self._entries.get(surface) or []
            if not entries:
                continue
            entry, ambiguous, reason = self._resolve(entries, text, start, end)
            out.append((entry, start, end, ambiguous, reason))
            cursor = end
        return out

    def _resolve(
        self, entries: List[VocabularyEntry], text: str, start: int, end: int
    ) -> Tuple[VocabularyEntry, bool, str]:
        """同名跨 label 时用倾向词判断；判断不出来按 Recipe 解释并标 ambiguous。"""
        if len(entries) == 1:
            return entries[0], False, "single"

        labels = {e.label for e in entries}
        if RECIPE in labels and INGREDIENT in labels:
            window = text[max(0, start - _HINT_WINDOW): end + _HINT_WINDOW]
            if any(h in window for h in _RECIPE_HINTS):
                return self._pick(entries, RECIPE), False, "recipe_hint"
            if any(h in window for h in _INGREDIENT_HINTS):
                return self._pick(entries, INGREDIENT), False, "ingredient_hint"
            return self._pick(entries, RECIPE), True, "default"

        # 同一 label 下多个节点同名（理论上重建图已不允许，保底）。
        # 但真实数据里有 7 组「某食材的别名 = 另一食材的正名」（甜椒/青椒、
        # 番茄/西红柿、茄子/青茄子、大料/八角、茴香/八角、马铃薯/土豆、
        # 香葱/葱），这时必须按正名解释，不能看谁先入表。
        best = min(entries, key=lambda e: _SOURCE_RANK.get(e.source, len(_SOURCE_RANK)))
        return best, False, "duplicate"

    @staticmethod
    def _pick(entries: List[VocabularyEntry], label: str) -> VocabularyEntry:
        for e in entries:
            if e.label == label:
                return e
        return entries[0]
