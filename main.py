"""
基于图RAG的智能烹饪助手 - 主程序
整合传统检索和图RAG检索，实现真正的图数据优势
"""

import os
import sys
import json
import time
import argparse
import logging
from typing import List, Optional

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from config import DEFAULT_CONFIG, GraphRAGConfig
from rag_modules import (
    GraphDataPreparationModule,
    MilvusIndexConstructionModule, 
    GenerationIntegrationModule
)
from rag_modules.hybrid_retrieval import HybridRetrievalModule
from rag_modules.graph_rag_retrieval import GraphRAGRetrieval
from rag_modules.intelligent_query_router import (
    IntelligentQueryRouter,
    QueryAnalysis,
    SearchStrategy,
)

# 加载环境变量
load_dotenv()

# --mode 取值 -> SearchStrategy；auto 表示走 LLM 路由
MODE_TO_STRATEGY = {
    "hybrid": SearchStrategy.HYBRID_TRADITIONAL,
    "graph": SearchStrategy.GRAPH_RAG,
    "combined": SearchStrategy.COMBINED,
}
MODE_CHOICES = ["auto", "hybrid", "graph", "combined"]

class AdvancedGraphRAGSystem:
    """
    图RAG系统
    
    核心特性：
    1. 智能路由：自动选择最适合的检索策略
    2. 双引擎检索：传统混合检索 + 图RAG检索
    3. 图结构推理：多跳遍历、子图提取、关系推理
    4. 查询复杂度分析：深度理解用户意图
    5. 自适应学习：基于反馈优化系统性能
    """
    
    def __init__(self, config: Optional[GraphRAGConfig] = None):
        self.config = config or DEFAULT_CONFIG
        
        # 核心模块
        self.data_module = None
        self.index_module = None
        self.generation_module = None
        
        # 检索引擎
        self.traditional_retrieval = None
        self.graph_rag_retrieval = None
        self.query_router = None
        
        # 系统状态
        self.system_ready = False
        
    def initialize_system(self):
        """初始化高级图RAG系统"""
        logger.info("启动高级图RAG系统...")
        
        try:
            # 1. 数据准备模块
            print("初始化数据准备模块...")
            self.data_module = GraphDataPreparationModule(
                uri=self.config.neo4j_uri,
                user=self.config.neo4j_user,
                password=self.config.neo4j_password,
                database=self.config.neo4j_database
            )
            
            # 2. 向量索引模块
            print("初始化Milvus向量索引...")
            self.index_module = MilvusIndexConstructionModule(
                host=self.config.milvus_host,
                port=self.config.milvus_port,
                collection_name=self.config.milvus_collection_name,
                dimension=self.config.milvus_dimension,
                model_name=self.config.embedding_model,
                uri=self.config.milvus_lite_path or None,
                embedding_device=self.config.embedding_device,
            )
            
            # 3. 生成模块
            print("初始化生成模块...")
            self.generation_module = GenerationIntegrationModule(
                model_name=self.config.llm_model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                base_url=self.config.llm_base_url,
                api_key_env=self.config.llm_api_key_env,
            )
            
            # 4. 传统混合检索模块
            print("初始化传统混合检索...")
            self.traditional_retrieval = HybridRetrievalModule(
                config=self.config,
                milvus_module=self.index_module,
                data_module=self.data_module,
                llm_client=self.generation_module.client
            )
            
            # 5. 图RAG检索模块
            print("初始化图RAG检索引擎...")
            self.graph_rag_retrieval = GraphRAGRetrieval(
                config=self.config,
                llm_client=self.generation_module.client
            )
            
            # 6. 智能查询路由器
            print("初始化智能查询路由器...")
            self.query_router = IntelligentQueryRouter(
                traditional_retrieval=self.traditional_retrieval,
                graph_rag_retrieval=self.graph_rag_retrieval,
                llm_client=self.generation_module.client,
                config=self.config
            )
            
            print("✅ 高级图RAG系统初始化完成！")
            
        except Exception as e:
            logger.error(f"系统初始化失败: {e}")
            raise
    
    def build_knowledge_base(self):
        """构建知识库（如果需要）"""
        print("\n检查知识库状态...")
        
        try:
            # 检查Milvus集合是否存在
            if self.index_module.has_collection():
                print("✅ 发现已存在的知识库，尝试加载...")
                if self.index_module.load_collection():
                    print("知识库加载成功！")
                    
                    # 重要：即使从已存在的知识库加载，也需要加载图数据以支持图索引
                    print("加载图数据以支持图检索...")
                    self.data_module.load_graph_data()
                    print("构建菜谱文档...")
                    self.data_module.build_recipe_documents()
                    print("进行文档分块...")
                    chunks = self.data_module.chunk_documents(
                        chunk_size=self.config.chunk_size,
                        chunk_overlap=self.config.chunk_overlap
                    )
                    
                    self._initialize_retrievers(chunks)
                    return
                else:
                    print("❌ 知识库加载失败，开始重建...")
            
            print("未找到已存在的集合，开始构建新的知识库...")
            
            # 从Neo4j加载图数据
            print("从Neo4j加载图数据...")
            self.data_module.load_graph_data()
            
            # 构建菜谱文档
            print("构建菜谱文档...")
            self.data_module.build_recipe_documents()
            
            # 进行文档分块
            print("进行文档分块...")
            chunks = self.data_module.chunk_documents(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap
            )
            
            # 构建Milvus向量索引
            print("构建Milvus向量索引...")
            if not self.index_module.build_vector_index(chunks):
                raise Exception("构建向量索引失败")
            
            # 初始化检索器
            self._initialize_retrievers(chunks)
            
            # 显示统计信息
            self._show_knowledge_base_stats()
            
            print("✅ 知识库构建完成！")
            
        except Exception as e:
            logger.error(f"知识库构建失败: {e}")
            raise
    
    def _initialize_retrievers(self, chunks: List = None):
        """初始化检索器"""
        print("初始化检索引擎...")
        
        # 如果没有chunks，从数据模块获取
        if chunks is None:
            chunks = self.data_module.chunks or []
        
        # 初始化传统检索器
        self.traditional_retrieval.initialize(chunks)
        
        # 初始化图RAG检索器
        self.graph_rag_retrieval.initialize()
        
        self.system_ready = True
        print("✅ 检索引擎初始化完成！")
    
    def _show_knowledge_base_stats(self):
        """显示知识库统计信息"""
        print(f"\n知识库统计:")
        
        # 数据统计
        stats = self.data_module.get_statistics()
        print(f"   菜谱数量: {stats.get('total_recipes', 0)}")
        print(f"   食材数量: {stats.get('total_ingredients', 0)}")
        print(f"   烹饪步骤: {stats.get('total_cooking_steps', 0)}")
        print(f"   文档数量: {stats.get('total_documents', 0)}")
        print(f"   文本块数: {stats.get('total_chunks', 0)}")
        
        # Milvus统计
        milvus_stats = self.index_module.get_collection_stats()
        print(f"   向量索引: {milvus_stats.get('row_count', 0)} 条记录")
        
        # 图RAG统计
        route_stats = self.query_router.get_route_statistics()
        print(f"   路由统计: 总查询 {route_stats.get('total_queries', 0)} 次")
        
        if stats.get('categories'):
            categories = list(stats['categories'].keys())[:10]
            print(f"   🏷️ 主要分类: {', '.join(categories)}")
    
    def ask_question_with_routing(self, question: str, stream: bool = False, explain_routing: bool = False):
        """
        智能问答：自动选择最佳检索策略
        """
        if not self.system_ready:
            raise ValueError("系统未就绪，请先构建知识库")
            
        print(f"\n❓ 用户问题: {question}")
        
        # 显示路由决策解释（可选）
        if explain_routing:
            explanation = self.query_router.explain_routing_decision(question)
            print(explanation)
        
        start_time = time.time()
        
        try:
            # 1. 智能路由检索
            print("执行智能查询路由...")
            relevant_docs, analysis = self.query_router.route_query(question, self.config.top_k)
            
            # 2. 显示路由信息
            strategy_icons = {
                "hybrid_traditional": "🔍",
                "graph_rag": "🕸️", 
                "combined": "🔄"
            }
            strategy_icon = strategy_icons.get(analysis.recommended_strategy.value, "❓")
            print(f"{strategy_icon} 使用策略: {analysis.recommended_strategy.value}")
            print(f"📊 复杂度: {analysis.query_complexity:.2f}, 关系密集度: {analysis.relationship_intensity:.2f}")
            
            # 3. 显示检索结果信息
            if relevant_docs:
                doc_info = []
                for doc in relevant_docs:
                    recipe_name = doc.metadata.get('recipe_name', '未知内容')
                    search_type = doc.metadata.get('search_type', doc.metadata.get('route_strategy', 'unknown'))
                    score = doc.metadata.get('final_score', doc.metadata.get('relevance_score', 0))
                    doc_info.append(f"{recipe_name}({search_type}, {score:.3f})")
                
                print(f"📋 找到 {len(relevant_docs)} 个相关文档: {', '.join(doc_info[:3])}")
                if len(doc_info) > 3:
                    print(f"    等 {len(relevant_docs)} 个结果...")
            else:
                # 保持返回值签名一致：始终返回 (result, analysis)
                return "抱歉，没有找到相关的烹饪信息。请尝试其他问题。", analysis
            
            # 4. 生成回答
            print("🎯 智能生成回答...")
            
            if stream:
                try:
                    for chunk_text in self.generation_module.generate_adaptive_answer_stream(question, relevant_docs):
                        print(chunk_text, end="", flush=True)
                    print("\n")
                    result = "流式输出完成"
                except Exception as stream_error:
                    logger.error(f"流式输出过程中出现错误: {stream_error}")
                    print(f"\n⚠️ 流式输出中断，切换到标准模式...")
                    # 使用非流式作为后备
                    result = self.generation_module.generate_adaptive_answer(question, relevant_docs)
            else:
                result = self.generation_module.generate_adaptive_answer(question, relevant_docs)
            
            # 5. 性能统计
            end_time = time.time()
            print(f"\n⏱️ 问答完成，耗时: {end_time - start_time:.2f}秒")
            
            return result, analysis
            
        except Exception as e:
            logger.error(f"问答处理失败: {e}")
            return f"抱歉，处理问题时出现错误：{str(e)}", None
    

    

    
    def ask_question(self, question: str, mode: str = "auto", trace_path: Optional[str] = None):
        """非交互问答入口。

        mode:
            auto     - 走 LLM 智能路由（现有行为）
            hybrid   - 强制仅传统混合检索（B1）
            graph    - 强制仅图检索（B2）
            combined - 强制两路合并（B4）

        返回 dict，同时按需追加一行 JSON 到 trace_path。
        """
        if not self.system_ready:
            raise ValueError("系统未就绪，请先构建知识库")

        if mode not in MODE_CHOICES:
            raise ValueError(f"未知 mode: {mode}，可选 {MODE_CHOICES}")

        started = time.time()
        analysis = None

        if mode == "auto":
            docs, analysis = self.query_router.route_query(question, self.config.top_k)
        else:
            docs = self.query_router.search_with_strategy(
                question, MODE_TO_STRATEGY[mode], self.config.top_k
            )

        retrieved_ms = int((time.time() - started) * 1000)

        if not docs:
            answer = "抱歉，没有找到相关的烹饪信息。请尝试其他问题。"
        else:
            answer = self.generation_module.generate_adaptive_answer(question, docs)

        latency_ms = int((time.time() - started) * 1000)

        record = {
            "question": question,
            "mode": mode,
            "strategy": analysis.recommended_strategy.value if analysis else mode,
            "num_docs": len(docs),
            "docs": [
                {
                    "recipe": d.metadata.get("recipe_name", ""),
                    "node_id": d.metadata.get("recipe_id", d.metadata.get("node_id", "")),
                    "score": d.metadata.get("final_score", d.metadata.get("relevance_score", 0)),
                    "source": d.metadata.get("search_type", d.metadata.get("search_source", "")),
                }
                for d in docs
            ],
            "answer": answer,
            "retrieved_ms": retrieved_ms,
            "latency_ms": latency_ms,
            "llm_model": self.config.llm_model,
            # 分段耗时（hybrid_search 写入；其他 mode 可能为空）
            "timing": next(
                (d.metadata.get("timing") for d in docs if d.metadata.get("timing")),
                None,
            ),
        }

        if trace_path:
            os.makedirs(os.path.dirname(trace_path) or ".", exist_ok=True)
            with open(trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record

    def run_batch(self, path: str, mode: str = "auto", trace_path: Optional[str] = None,
                  limit: Optional[int] = None) -> List[dict]:
        """批量跑一组问题。输入支持 .jsonl（每行含 question）或 .txt（每行一个问题）。"""
        questions = []
        with open(path, encoding="utf-8") as f:
            if path.endswith(".jsonl"):
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    questions.append(obj.get("question") or obj.get("q") or "")
            else:
                questions = [line.strip() for line in f if line.strip()]

        if limit:
            questions = questions[:limit]

        results = []
        for i, q in enumerate(questions, 1):
            print(f"\n[{i}/{len(questions)}] {q}")
            try:
                rec = self.ask_question(q, mode=mode, trace_path=trace_path)
                print(f"   -> {len(rec['docs'])} docs, {rec['latency_ms']} ms")
                results.append(rec)
            except Exception as e:
                logger.error(f"问题处理失败: {q} -> {e}")
                results.append({"question": q, "error": str(e)})
        return results

    def write_config_snapshot(self, path: str = "results/config_snapshot.json"):
        """把运行时配置落盘，便于复现（密码已脱敏）。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        snapshot = {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "config": self.config.to_dict(mask_secrets=True),
            "modes": MODE_CHOICES,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"配置快照已写入 {path}")

    def run_interactive(self):
        """运行交互式问答"""
        if not self.system_ready:
            print("❌ 系统未就绪，请先构建知识库")
            return
            
        print("\n欢迎使用尝尝咸淡RAG烹饪助手！")
        print("可用功能：")
        print("   - 'stats' : 查看系统统计")
        print("   - 'rebuild' : 重建知识库")
        print("   - 'quit' : 退出系统")
        print("\n" + "="*50)
        
        while True:
            try:
                user_input = input("\n您的问题: ").strip()
                
                if not user_input:
                    continue
                    
                if user_input.lower() == 'quit':
                    break
                elif user_input.lower() == 'stats':
                    self._show_system_stats()
                    continue
                elif user_input.lower() == 'rebuild':
                    self._rebuild_knowledge_base()
                    continue
                
                # 普通问答 - 使用默认设置
                use_stream = True  # 默认使用流式输出
                explain_routing = False  # 默认不显示路由决策

                print("\n回答:")
                
                result, analysis = self.ask_question_with_routing(
                    user_input, 
                    stream=use_stream, 
                    explain_routing=explain_routing
                )
                
                if not use_stream and result:
                    print(f"{result}\n")
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"处理问题时出错: {e}")
                import traceback
                traceback.print_exc()
        
        print("\n👋 感谢使用尝尝咸淡RAG烹饪助手！")
        self._cleanup()
    
    def _show_system_stats(self):
        """显示系统统计信息"""
        print("\n系统运行统计")
        print("=" * 40)
        
        # 路由统计
        route_stats = self.query_router.get_route_statistics()
        total_queries = route_stats.get('total_queries', 0)
        
        if total_queries > 0:
            print(f"总查询次数: {total_queries}")
            print(f"传统检索: {route_stats.get('traditional_count', 0)} ({route_stats.get('traditional_ratio', 0):.1%})")
            print(f"图RAG检索: {route_stats.get('graph_rag_count', 0)} ({route_stats.get('graph_rag_ratio', 0):.1%})")
            print(f"组合策略: {route_stats.get('combined_count', 0)} ({route_stats.get('combined_ratio', 0):.1%})")
        else:
            print("暂无查询记录")
        
        # 知识库统计
        self._show_knowledge_base_stats()
    
    def _rebuild_knowledge_base(self):
        """重建知识库"""
        print("\n准备重建知识库...")
        
        # 确认操作
        confirm = input("⚠️  这将删除现有的向量数据并重新构建，是否继续？(y/N): ").strip().lower()
        if confirm != 'y':
            print("❌ 重建操作已取消")
            return
        
        try:
            print("删除现有的Milvus集合...")
            if self.index_module.delete_collection():
                print("✅ 现有集合已删除")
            else:
                print("删除集合时出现问题，继续重建...")
            
            # 重新构建知识库
            print("开始重建知识库...")
            self.build_knowledge_base()
            
            print("✅ 知识库重建完成！")
            
        except Exception as e:
            logger.error(f"重建知识库失败: {e}")
            print(f"❌ 重建失败: {e}")
            print("建议：请检查Milvus服务状态后重试")
    
    def _cleanup(self):
        """清理资源"""
        if self.data_module:
            self.data_module.close()
        if self.traditional_retrieval:
            self.traditional_retrieval.close()
        if self.graph_rag_retrieval:
            self.graph_rag_retrieval.close()
        if self.index_module:
            self.index_module.close()

def parse_args(argv=None):
    """命令行参数。不带参数时保持原来的交互式行为。"""
    parser = argparse.ArgumentParser(
        description="Evidence-Guided Agentic GraphRAG - 烹饪知识问答"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--question", "-q", type=str, help="单条问题，跑完即退出")
    group.add_argument("--batch", "-b", type=str, help="批量问题文件（.jsonl 或 .txt，每行一个问题）")

    parser.add_argument(
        "--mode", "-m",
        choices=MODE_CHOICES,
        default="auto",
        help="检索引擎选择：auto=LLM路由，hybrid=B1，graph=B2，combined=B4",
    )
    parser.add_argument("--top-k", type=int, default=None, help="检索条数，默认取配置")
    parser.add_argument("--limit", type=int, default=None, help="批量模式下最多跑几条")
    parser.add_argument(
        "--trace",
        type=str,
        default=None,
        help="trace 落盘路径（jsonl），默认 results/smoke_trace.jsonl（非交互模式）",
    )
    parser.add_argument("--no-build", action="store_true", help="跳过知识库构建（已构建过时更快）")
    parser.add_argument("--stream", action="store_true", help="单条模式下流式输出")
    return parser.parse_args(argv)


def main(argv=None):
    """主函数"""
    args = parse_args(argv)

    try:
        print("启动高级图RAG系统...")

        config = GraphRAGConfig()
        if args.top_k:
            config.top_k = args.top_k

        rag_system = AdvancedGraphRAGSystem(config=config)
        rag_system.initialize_system()

        if not args.no_build:
            rag_system.build_knowledge_base()

        # 配置落盘，保证可复现
        rag_system.write_config_snapshot()

        # 非交互模式：trace 默认落到 results/smoke_trace.jsonl
        trace_path = args.trace
        if trace_path is None and (args.question or args.batch):
            trace_path = "results/smoke_trace.jsonl"

        if args.question:
            record = rag_system.ask_question(args.question, mode=args.mode, trace_path=trace_path)
            print("\n" + "=" * 60)
            print(f"问题: {record['question']}")
            print(f"模式: {record['mode']}  检索文档: {record['num_docs']}  耗时: {record['latency_ms']} ms")
            print("=" * 60)
            print(record["answer"])
            print("=" * 60)
            if trace_path:
                print(f"trace 已追加: {trace_path}")
            return 0

        if args.batch:
            results = rag_system.run_batch(
                args.batch, mode=args.mode, trace_path=trace_path, limit=args.limit
            )
            ok = sum(1 for r in results if "error" not in r)
            print(f"\n批量完成: {ok}/{len(results)} 成功")
            if trace_path:
                print(f"trace 已追加: {trace_path}")
            return 0

        # 无参数：保持交互式
        rag_system.run_interactive()
        return 0

    except Exception as e:
        logger.error(f"系统运行失败: {e}")
        import traceback
        traceback.print_exc()
        print(f"\n❌ 系统错误: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
    main() 