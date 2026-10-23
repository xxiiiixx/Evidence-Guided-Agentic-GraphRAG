"""从图谱导出实体名单，供「闭集提示词」把候选直接交给大模型。

这个模块**不做匹配**：没有 Aho-Corasick，没有倾向词消歧，没有打分。
它只做两件事：

1. 把图上真实存在的名字（含别名、菜谱分类、菜系、难度档位）整理成提示词要的字符串；
2. 把模型返回的规范名换回 `nodeId`，让下游能按节点取数据。

为什么不做匹配：词表匹配能覆盖的只有字面命中的写法，而「松花蛋→皮蛋」这类
等价表达、以及任何没有实体名的意图类问句，它都无能为力。这类判断交给模型，
名单只用来**约束模型不许编**（见 `llm_entity_extractor.py`）。
"""

from __future__ import annotations

import ast
import csv
import json
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

RECIPE = "Recipe"
INGREDIENT = "Ingredient"
TOPIC = "Topic"

LABEL_ORDER = (RECIPE, INGREDIENT, TOPIC)

# 图里 DifficultyLevel 节点名是一星…五星，而 Recipe.difficulty 是 1.0–5.0 的浮点
DIFFICULTY_TIERS = {1: "一星", 2: "二星", 3: "三星", 4: "四星", 5: "五星"}

_SPLIT_CHARS = "、,;/|"
_ASCII = re.compile(r"[A-Za-z0-9]")

# 别名不是越多越好：这份数据里有大量机器生成的怪别名（「tomato土豆炖牛肉」
# 「sweet and sour汁」），塞进提示词既费 token 又会诱导模型照抄。只留干净的短名。
MAX_ALIAS_LEN = 12
MAX_ALIASES_PER_ENTRY = 3


def _clean_aliases(aliases: Iterable[str]) -> Tuple[str, ...]:
    kept: List[str] = []
    for raw in aliases:
        alias = (raw or "").strip()
        if not alias or len(alias) > MAX_ALIAS_LEN or _ASCII.search(alias):
            continue
        if alias not in kept:
            kept.append(alias)
        if len(kept) >= MAX_ALIASES_PER_ENTRY:
            break
    return tuple(kept)


# ------------------------------------------------------------------ 数据类


@dataclass(frozen=True)
class VocabularyEntry:
    """名单里的一个候选。`name` 是规范名，也是提示词里要求模型输出的字符串。"""

    name: str
    label: str
    node_id: Optional[str] = None
    aliases: Tuple[str, ...] = ()
    source: str = "name"  # name / alias / category / cuisine / difficulty

    def display(self) -> str:
        """提示词里的写法：只有规范名，不带括号别名。

        别名不进提示词（省 1 098 个字符，见 `build()` 的说明），但仍留在
        `_alias_index` 里，用来把模型自己写出来的旧名换回规范名——这部分不占 token。
        """
        return self.name


@dataclass
class Match:
    """模型选中一个候选。`surface` 是模型原样复制回来的字符串。"""

    entry: VocabularyEntry
    surface: str = ""
    ambiguous: bool = False
    reason: str = ""

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
            "surface": self.surface or self.entry.name,
            "name": self.entry.name,
            "label": self.entry.label,
            "node_id": self.entry.node_id,
            "ambiguous": self.ambiguous,
        }


@dataclass
class KeywordResult:
    """一次关键词抽取的结果。下游只认这个结构，不关心是谁产出的。"""

    query: str
    entities: List[Match] = field(default_factory=list)
    topics: List[Match] = field(default_factory=list)
    elapsed_ms: float = 0.0
    vocabulary: Dict[str, int] = field(default_factory=dict)
    keyword_source: str = "llm_vocab"
    raw: str = ""
    dropped: List[str] = field(default_factory=list)
    prompt_chars: int = 0
    attempts: int = 0

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
        ents = ", ".join(f"{m.name}({m.label})" for m in self.entities) or "-"
        tops = ", ".join(self.topic_names) or "-"
        return (
            f"entities=[{ents}] topics=[{tops}] dropped={self.dropped or '-'} "
            f"attempts={self.attempts} {self.elapsed_ms:.2f}ms"
        )

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "keyword_source": self.keyword_source,
            "link_elapsed_ms": round(self.elapsed_ms, 3),
            "link_vocabulary": self.vocabulary,
            "matched_entities": [m.to_metadata() for m in self.entities],
            "matched_topics": [m.to_metadata() for m in self.topics],
            "dropped_surfaces": list(self.dropped),
            "prompt_chars": self.prompt_chars,
            "attempts": self.attempts,
        }


# ------------------------------------------------------------------ 解析


def _split(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "[]"):
        return []
    for ch in _SPLIT_CHARS:
        text = text.replace(ch, " ")
    return [t for t in text.split() if t]


def parse_aliases(raw: Any) -> List[str]:
    """`synonyms` 字段：JSON（[{"term": "大料"}] 或 ["大料"]）或分隔文本。"""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none", "[]"):
        return []
    try:
        payload = json.loads(text) if text[0] in "[{" else ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return _split(text)
    out: List[str] = []
    if isinstance(payload, dict):
        payload = [payload]
    if isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, dict):
                for key in ("term", "name", "alias", "synonym"):
                    if item.get(key):
                        out.append(str(item[key]).strip())
                        break
            elif item:
                out.append(str(item).strip())
    return [a for a in out if a]


def parse_merged_aliases(raw: Any) -> List[str]:
    """Day 3 同义词归一的产物：规范名是「姜」，被并进来的原名「生姜」记在这里。

    只是文本字段（不是 JSON）。漏读它，问句里的「生姜」就永远选不中「姜」。
    """
    return _split(raw)


# ------------------------------------------------------------------ 词表


class GraphVocabulary:
    """图上名字的只读索引。"""

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str], VocabularyEntry] = {}
        self._alias_index: Dict[Tuple[str, str], str] = {}
        self._stats: Dict[str, int] = {}

    # ---------------------------------------------------------- 构建

    def _add(self, name: str, label: str, node_id: Optional[str], aliases: Iterable[str], source: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        key = (label, name)
        merged = _clean_aliases(a for a in aliases if a and a.strip() != name)
        if key in self._entries:
            old = self._entries[key]
            merged = tuple(dict.fromkeys(list(old.aliases) + list(merged)))
            node_id = old.node_id or node_id
            if old.source != "name":
                source = old.source if source == "name" else source
        self._entries[key] = VocabularyEntry(
            name=name, label=label, node_id=node_id, aliases=merged, source=source
        )
        for alias in merged:
            self._alias_index.setdefault((label, alias), name)

    def build(self, recipes: Iterable[Any], ingredients: Iterable[Any]) -> "GraphVocabulary":
        n_recipes = n_ingredients = n_aliases = 0

        # 别名只进内部索引，不进提示词（提示词里只印规范名）：
        # - `mergedAliases` 是 Day 3 同义词归一的产物（蒜→大蒜、生姜→姜…），
        #   不记下来，模型写「生姜」时就换不回「姜」；
        # - `synonyms` 覆盖只有 9.7%，且多为机器生成的脏值（「清清蒸生蚝」
        #   「清steamed生蚝」），印出来只会费 token、诱导照抄；留在索引里则零成本。

        for node in recipes:
            props = getattr(node, "properties", {}) or {}
            name = getattr(node, "name", "") or props.get("name", "")
            node_id = getattr(node, "node_id", None)
            aliases = parse_aliases(props.get("synonyms")) + parse_merged_aliases(props.get("mergedAliases"))
            self._add(name, RECIPE, node_id, aliases, "name")
            n_aliases += len(aliases)
            n_recipes += 1

            # 分类 / 菜系 / 难度：topic_level_retrieval 就是按这三样查的
            for token in _split(props.get("recipeCategory")):
                self._add(token, TOPIC, None, (), "category")
            for token in _split(props.get("cuisineType")):
                self._add(token, TOPIC, None, (), "cuisine")
            tier = _difficulty_tier(props.get("difficulty"))
            if tier:
                self._add(tier, TOPIC, None, (), "difficulty")

        for node in ingredients:
            props = getattr(node, "properties", {}) or {}
            name = getattr(node, "name", "") or props.get("name", "")
            node_id = getattr(node, "node_id", None)
            aliases = parse_aliases(props.get("synonyms")) + parse_merged_aliases(props.get("mergedAliases"))
            self._add(name, INGREDIENT, node_id, aliases, "name")
            n_aliases += len(aliases)
            n_ingredients += 1

        self._stats = {
            "recipes": n_recipes,
            "ingredients": n_ingredients,
            "entries": len(self._entries),
            "recipeEntries": sum(1 for k in self._entries if k[0] == RECIPE),
            "ingredientEntries": sum(1 for k in self._entries if k[0] == INGREDIENT),
            "topicEntries": sum(1 for k in self._entries if k[0] == TOPIC),
            "aliasSurfaces": len(self._alias_index),
            "aliasRefs": sum(len(e.aliases) for e in self._entries.values()),
            "aliasRefsRaw": n_aliases,
        }
        logger.info("图谱名单构建完成: %s", self._stats)
        return self

    @classmethod
    def from_nodes_csv(cls, path: str) -> "GraphVocabulary":
        """只读 Day 3 的产物建名单，不连 Neo4j —— 离线测提示词用。"""
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return cls.from_nodes_rows(csv.DictReader(fh))

    @classmethod
    def from_nodes_rows(cls, rows: Iterable[Dict[str, Any]]) -> "GraphVocabulary":
        """从 nodes.csv 的行建名单。单独抽出来是为了能在内存里测，不落盘。"""
        recipes: List[Any] = []
        ingredients: List[Any] = []
        for row in rows:
            label = (row.get("labels") or "").strip()
            if label not in (RECIPE, INGREDIENT):
                continue
            props = {k: v for k, v in row.items() if k not in ("nodeId", "labels", "name")}
            node = _Node(node_id=row.get("nodeId", ""), name=row.get("name", ""), properties=props)
            (recipes if label == RECIPE else ingredients).append(node)
        return cls().build(recipes, ingredients)

    # ---------------------------------------------------------- 查询

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def names_by_label(self) -> Dict[str, List[str]]:
        """按 label 分组的规范名（排序）。提示词里给模型看的就是它。"""
        groups: Dict[str, List[str]] = {label: [] for label in LABEL_ORDER}
        for (label, name) in self._entries:
            groups.setdefault(label, []).append(name)
        return {label: sorted(names) for label, names in groups.items()}

    def display_by_label(self) -> Dict[str, List[str]]:
        """同 `names_by_label`，但带别名：`姜（生姜、老姜）`。"""
        groups: Dict[str, List[str]] = {label: [] for label in LABEL_ORDER}
        for entry in self._entries.values():
            groups.setdefault(entry.label, []).append(entry.display())
        return {label: sorted(items) for label, items in groups.items()}

    def entry_for(self, name: str, label: str) -> Optional[VocabularyEntry]:
        """规范名或别名 → 条目。模型输出的是规范名，这里也容忍它照抄了别名。"""
        name = (name or "").strip()
        if not name:
            return None
        entry = self._entries.get((label, name))
        if entry is not None:
            return entry
        canonical = self._alias_index.get((label, name))
        return self._entries.get((label, canonical)) if canonical else None


# ------------------------------------------------------------------ 辅助


def _difficulty_tier(value: Any) -> Optional[str]:
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return DIFFICULTY_TIERS.get(num)


@dataclass
class _Node:
    node_id: str
    name: str
    properties: Dict[str, Any]
