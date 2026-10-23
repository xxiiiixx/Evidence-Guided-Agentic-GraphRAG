"""闭集实体抽取：把图谱名单整份交给大模型，让它只做选择、不做生成。

## 为什么要有这个模块

Day 2 的 `extract_query_keywords()` 提示词里写着：

> 1. 实体级关键词：具体的食材、菜品名称、工具、品牌等有形实体
>    - 例如：鸡胸肉、西兰花、红烧肉、平底锅、老干妈
>    - **对于抽象查询，推测相关的具体食材/菜品**

最后那句是本末倒置：它明确要求模型去**编**图谱里可能并不存在的名字。模型照做了，
于是「红烧肉」（图上只有红烧冬瓜/红烧猪蹄/红烧茄子）这类名字会被生成出来，
下游按名字查索引必然整条落空，而且不报错。所以 Day 2 的问题不是「用了大模型」，
而是**提示词没有给模型任何约束**。

## 这里的做法

把图上真实存在的名字按类型列进提示词，规则改成「只能逐字复制名单里的字符串」
「名单里没有就返回空数组」「不要推测」。模型的输出因此天然可对齐：
每条命中都能换回一个真实节点和 nodeId，不存在「名字对了但节点不存在」。

名单由 `graph_vocabulary.py` 从图谱导出，不做任何匹配 —— 判断交给模型，
名单只负责划定它不许越过的边界。

## 代价

- 提示词变长（约 5.4k 字符），每条 query 一次模型调用；
- 词表作为静态前缀放在最前面、问题放在最后，便于上游做 prefix cache；
- 模型若返回名单外的名字，会被记进 `dropped` 并丢弃，不会进检索路径。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .graph_vocabulary import (
    INGREDIENT,
    LABEL_ORDER,
    RECIPE,
    TOPIC,
    GraphVocabulary,
    KeywordResult,
    Match,
)

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?|```", re.IGNORECASE)

# 静态前缀：名单在前、问题在后，方便上游按前缀缓存命中
PROMPT_HEAD = """你是实体链接器，不是实体抽取器。下面按类型给出知识图谱中真实存在的**全部**名字。

任务：只从这些名单里挑出与用户问题相关的名字。

硬性规则：
1. 只能返回名单里的名字，逐字复制。不得改写、补全、翻译、简写、合并或纠正错别字。
2. 名单里只有规范名。问题里的说法若与某个名字同指（同义词、别称、旧写法、错别字），返回那个规范名本身；不要输出解释、括号或别名。
3. 名单里没有对应的名字时返回空数组；不得用近义词、俗称、相似说法或自己编的名字代替。
4. 只挑问题里**明确提到**的名字；不得推测「可能相关」的食材、菜品或分类。
5. 每份名单最多 {max_per_type} 个，按相关性从高到低排列。
6. 只输出一个 JSON 对象，不要解释，不要代码块围栏。

输出格式：
{{"recipes": [], "ingredients": [], "topics": []}}

【菜谱名单｜{n_recipes} 个】
{recipes}

【食材名单｜{n_ingredients} 个】
{ingredients}

【主题名单｜{n_topics} 个｜菜谱分类、菜系、难度档位】
{topics}

用户问题："""

_KEYS: Tuple[Tuple[str, str], ...] = (
    (RECIPE, "recipes"),
    (INGREDIENT, "ingredients"),
    (TOPIC, "topics"),
)


class LLMEntityExtractor:
    """闭集抽取器。构造时把名单编译成静态前缀，之后每条 query 只拼一个后缀。"""

    def __init__(
        self,
        llm_client: Any,
        config: Any,
        vocabulary: GraphVocabulary,
        max_per_type: int = 8,
        max_retries: int = 1,
    ) -> None:
        if llm_client is None:
            raise ValueError("LLMEntityExtractor 需要 llm_client")
        self.llm_client = llm_client
        self.config = config
        self.vocabulary = vocabulary
        self.max_per_type = max_per_type
        self.max_retries = max_retries
        self._head = self._compile_head()

    # ------------------------------------------------------------------ 提示词

    def _compile_head(self) -> str:
        display = self.vocabulary.display_by_label()
        recipes = display.get(RECIPE, [])
        ingredients = display.get(INGREDIENT, [])
        topics = display.get(TOPIC, [])
        head = PROMPT_HEAD.format(
            max_per_type=self.max_per_type,
            n_recipes=len(recipes),
            n_ingredients=len(ingredients),
            n_topics=len(topics),
            recipes="、".join(recipes),
            ingredients="、".join(ingredients),
            topics="、".join(topics),
        )
        logger.info(
            "闭集提示词静态前缀构建完成: %d 字符（菜谱 %d / 食材 %d / 主题 %d）",
            len(head), len(recipes), len(ingredients), len(topics),
        )
        return head

    @property
    def prompt_head(self) -> str:
        return self._head

    def build_prompt(self, query: str) -> str:
        return f"{self._head}{query}"

    # ------------------------------------------------------------------ 调用

    def extract(self, query: str) -> KeywordResult:
        text = (query or "").strip()
        started = time.perf_counter()
        prompt = self.build_prompt(text)

        result = KeywordResult(
            query=text,
            vocabulary=dict(self.vocabulary.stats),
            keyword_source="llm_vocab",
            prompt_chars=len(prompt),
        )
        if not text:
            result.elapsed_ms = (time.perf_counter() - started) * 1000.0
            return result

        raw = ""
        payload: Optional[Dict[str, Any]] = None
        for attempt in range(1, self.max_retries + 2):
            result.attempts = attempt
            try:
                raw = self._call(prompt)
                payload = self._parse(raw)
                break
            except Exception as exc:  # 网络错误 / JSON 不合法 / 键名不对
                logger.warning("闭集抽取第 %d 次失败: %s", attempt, exc)
                payload = None

        result.raw = raw
        if payload is None:
            result.elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.error("闭集抽取失败，返回空结果（检索会退回向量 + BM25）")
            return result

        self._fill(result, payload, text)
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info("闭集抽取: %s", result.summary())
        return result

    def _call(self, prompt: str) -> str:
        response = self.llm_client.chat.completions.create(
            model=self.config.llm_model,
            messages=[{"role": "user", "content": prompt}],
            # 抽样只会削弱「逐字复制名单」这个约束
            temperature=0.0,
            max_tokens=256,
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _parse(raw: str) -> Dict[str, Any]:
        text = _FENCE.sub("", raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"响应里没有 JSON 对象: {text[:120]!r}")
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("顶层不是对象")
        if not any(key in payload for _label, key in _KEYS):
            # 模型退回旧提示词的键名时不能默默接受：那说明提示词没生效
            raise ValueError(f"缺少约定键名 {[k for _l, k in _KEYS]}: {list(payload)}")
        return payload

    def _fill(self, result: KeywordResult, payload: Dict[str, Any], text: str) -> None:
        seen: set = set()

        for label, key in _KEYS:
            names = payload.get(key) or []
            if isinstance(names, str):
                names = [names]
            if not isinstance(names, (list, tuple)):
                result.dropped.append(f"{label}:<{type(names).__name__}>")
                continue

            kept = 0
            for name in names:
                if kept >= self.max_per_type:
                    break
                if not isinstance(name, str):
                    result.dropped.append(f"{label}:<{type(name).__name__}>")
                    continue
                name = name.strip()
                if not name:
                    continue
                entry = self.vocabulary.entry_for(name, label)
                if entry is None:
                    # 不在名单里：记下来但不用。绝不拿它去模糊匹配，
                    # 那等于把「模型编的名字」重新放回检索路径。
                    result.dropped.append(f"{label}:{name}")
                    continue

                match = Match(
                    entry=entry,
                    surface=name,
                    ambiguous=False,
                    reason="llm_vocab",
                )
                if label == TOPIC:
                    if (label, entry.name) in seen:
                        continue
                    seen.add((label, entry.name))
                    result.topics.append(match)
                else:
                    if (label, entry.node_id or entry.name) in seen:
                        continue
                    seen.add((label, entry.node_id or entry.name))
                    result.entities.append(match)
                kept += 1

        if result.dropped:
            logger.warning(
                "模型返回了 %d 个名单外的名字（已丢弃）: %s",
                len(result.dropped), result.dropped,
            )
