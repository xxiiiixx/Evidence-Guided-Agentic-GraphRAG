"""
基于图数据库的RAG系统配置文件

所有可调参数均可通过环境变量覆盖，未设置时回退到默认值。
这样同一份代码可在不同环境跑，且运行配置可序列化落盘，保证实验可复现。
"""

import os
from dataclasses import dataclass
from typing import Dict, Any


def _env(name: str, default: str) -> str:
    """读取环境变量，空字符串视为未设置。"""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，非法值回退默认。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass
class GraphRAGConfig:
    """基于图数据库的RAG系统配置类"""

    # Neo4j数据库配置
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    neo4j_database: str = ""

    # Milvus配置
    milvus_host: str = ""
    milvus_port: int = 0
    # Milvus Lite 本地文件路径。非空则走嵌入式模式，忽略 host/port。
    # 注意：不能用 MILVUS_URI 作变量名——pymilvus 在 import 时会读取该变量
    # 并校验格式必须是 http(s)://，设成本地路径会导致 import 直接失败。
    milvus_lite_path: str = ""
    milvus_collection_name: str = "cooking_knowledge"
    milvus_dimension: int = 512  # BGE-small-zh-v1.5的向量维度

    # 模型配置
    embedding_model: str = ""
    embedding_device: str = ""     # cpu / cuda / cuda:0；cuda 不可用时自动降级
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key_env: str = ""

    # 检索配置（LightRAG Round-robin策略）
    top_k: int = 5

    # 图谱检索的关键词来源
    #   llm_vocab：闭集抽取（默认）——整份图谱名单进提示词，模型只做「逐字选择」
    #   llm_open ：旧行为，让 LLM 从问句里自由「猜」实体名（会编图上没有的名字），
    #              只保留给对照实验
    #   none     ：不抽关键词，只用向量 + BM25（消融基线）
    keyword_source: str = ""

    # 父文档检索配置
    enable_parent_doc_retrieval: bool = False  # 默认 False，不做父文档回填，直接把chunk当作上下文，有可能会出现步骤不全问题
    parent_doc_top_n: int = 3                   # 仅 RRF 分前 N 名做父文档替换
    parent_doc_max_chars: int = 4000            # 每篇父文档字符上限（兜底）

    # 生成配置
    temperature: float = 0.1
    max_tokens: int = 2048

    # 图数据处理配置
    chunk_size: int = 500
    chunk_overlap: int = 50
    max_graph_depth: int = 2  # 图遍历最大深度

    def __post_init__(self):
        """初始化后的处理：从环境变量加载，未设置则用默认值。"""
        self.neo4j_uri = self.neo4j_uri or _env("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = self.neo4j_user or _env("NEO4J_USER", "neo4j")
        self.neo4j_password = self.neo4j_password or _env("NEO4J_PASSWORD", "all-in-rag")
        self.neo4j_database = self.neo4j_database or _env("NEO4J_DATABASE", "neo4j")

        self.milvus_host = self.milvus_host or _env("MILVUS_HOST", "localhost")
        self.milvus_port = self.milvus_port or _env_int("MILVUS_PORT", 19530)
        # MILVUS_LITE_PATH 非空则走 Lite；为空则连 host:port 的远程服务
        self.milvus_lite_path = self.milvus_lite_path or _env("MILVUS_LITE_PATH", "")

        self.embedding_model = self.embedding_model or _env(
            "EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
        )
        # 默认 cpu 保证开箱可跑；有 GPU 的机器在 .env 里设 EMBEDDING_DEVICE=cuda
        self.embedding_device = self.embedding_device or _env("EMBEDDING_DEVICE", "cpu")
        # 默认值必须选魔搭推理 API 上有可用 provider 的模型。
        self.llm_model = self.llm_model or _env(
            "LLM_MODEL", "Qwen/Qwen3.8-Flash-Next"
        )
        self.llm_base_url = self.llm_base_url or _env(
            "LLM_BASE_URL", "https://api-inference.modelscope.cn/v1"
        )
        self.llm_api_key_env = self.llm_api_key_env or _env(
            "LLM_API_KEY_ENV", "MODELSCOPE_API_KEY"
        )

        # 留空时由 hybrid_retrieval 兜底为 linker，这里只负责读环境变量
        self.keyword_source = (self.keyword_source or _env("KEYWORD_SOURCE", "")).strip().lower()

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'GraphRAGConfig':
        """从字典创建配置对象"""
        return cls(**config_dict)

    @classmethod
    def from_env(cls) -> 'GraphRAGConfig':
        """从环境变量构造配置（读取逻辑在 __post_init__）。"""
        return cls()

    def api_key(self) -> str:
        """按 llm_api_key_env 指定的变量名取 API key。"""
        return os.getenv(self.llm_api_key_env, "")

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """转换为字典。

        mask_secrets=True 时密码替换为 ***，便于落盘与日志。
        """
        password = "***" if (mask_secrets and self.neo4j_password) else self.neo4j_password
        return {
            'neo4j_uri': self.neo4j_uri,
            'neo4j_user': self.neo4j_user,
            'neo4j_password': password,
            'neo4j_database': self.neo4j_database,
            'milvus_host': self.milvus_host,
            'milvus_port': self.milvus_port,
            'milvus_lite_path': self.milvus_lite_path,
            'milvus_collection_name': self.milvus_collection_name,
            'milvus_dimension': self.milvus_dimension,
            'embedding_model': self.embedding_model,
            'embedding_device': self.embedding_device,
            'llm_model': self.llm_model,
            'llm_base_url': self.llm_base_url,
            'llm_api_key_env': self.llm_api_key_env,
            'top_k': self.top_k,
            'keyword_source': self.keyword_source,
            'enable_parent_doc_retrieval': self.enable_parent_doc_retrieval,
            'parent_doc_top_n': self.parent_doc_top_n,
            'parent_doc_max_chars': self.parent_doc_max_chars,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'chunk_size': self.chunk_size,
            'chunk_overlap': self.chunk_overlap,
            'max_graph_depth': self.max_graph_depth,
        }


# 默认配置实例（构造时读取环境变量）
DEFAULT_CONFIG = GraphRAGConfig()
