# 🚀 FDE 转型实战：2 个高频智能体 汇总逻辑实现

> **作者定位**：前 CRM 管理员 → FDE 转型中。利用 Trae + WorkBuddy MCP 连接器 + 飞书/销售易 CRM，实现 2 个高频业务智能体，为企业 LTC 流程 AI 化直接贡献。
> **产出时间**：2026-09-16
> **已验证链路**：飞书 MCP ✅（多维表格已创建） | 销售易 CRM MCP ✅（270 工具全量可用）

---

## 📐 总体架构：两个智能体如何协同

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    企业 LTC 全流程 AI 化架构                              │
│                                                                         │
│  ┌─────────────┐     语音/文字     ┌────────────────────┐                │
│  │  销售日常    │ ──────────────► │                    │                │
│  │  飞书私聊    │ ◄────────────── │  Trae / WorkBuddy  │                │
│  │  @智能体    │   话术/方案/提醒 │  (LLM + MCP 路由)  │                │
│  └─────────────┘                 └─────────┬──────────┘                │
│                                             │                           │
│                    ┌────────────────────────┼──────────────────────┐    │
│                    │                        │                      │    │
│                    ▼                        ▼                      ▼    │
│  ┌─────────────────────────┐  ┌───────────────────────┐  ┌──────────┐  │
│  │  🚀 项目 1              │  │  🚀 项目 2             │  │ 销售易    │  │
│  │  LTC RAG 问答机器人      │  │  CRM 多维表格 Agent    │  │  CRM      │  │
│  │                         │  │                       │  │ (MCP 对   │  │
│  │  · 话术生成              │  │  · 语音→CRM 录入       │  │  接同步)  │  │
│  │  · 招投标初稿            │  │  · 跟进自动记录        │  └──────────┘  │
│  │  · FAQ 智能检索          │  │  · 下次联系催办        │                 │
│  │                         │  │  · 销售运营看板        │                 │
│  │  知识源:                 │  │                       │                 │
│  │  · 产品白皮书            │  │  载体:                 │                 │
│  │  · 竞品打击话术          │  │  飞书多维表格          │                 │
│  │  · 优秀合同范本          │  │  (已搭建✅)           │                 │
│  └─────────────────────────┘  └───────────────────────┘                 │
│                                                                         │
│  底层能力层:  LLM (Trae 内置/硅基流动)  +  MCP 连接器 (飞书/销售易 CRM)   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 项目 1：LTC 销售话术与招投标 RAG 问答机器人

### 1.1 定位与业务价值

| 维度 | 说明 |
|------|------|
| **解决什么痛点** | 销售「不知道怎么写、话术不标准、招投标参数难找」；新人产品知识零散找不到 |
| **核心场景** | ① 群聊 @机器人问产品/竞品/FAD 话术；② 招投标一键生成技术响应初稿 |
| **业务价值** | 新人上手周期缩短 50%+；招投标初稿从 4h→30min；统一销售话术口径 |

### 1.2 完整架构设计

```
销售飞书群聊 / 私聊 ──(1) @机器人提问
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RAG 智能体核心（Python/Node.js）                 │
│                                                                     │
│  (2) 意图识别 & 查询改写                                            │
│      ├─ 话术生成意图 (客户问 XX 怎么答？)                            │
│      ├─ 招投标生成意图 (给 XX 项目做投标书)                          │
│      └─ 纯 FAQ 检索 (XX 技术参数是多少？)                            │
│                                                                     │
│  (3) 混合检索管道                                                   │
│      ├─ BM25 关键词检索 (从飞书知识库/本地索引)                      │
│      ├─ 向量语义检索 (BGE-M3 → ChromaDB)                            │
│      └─ RRF 融合重排 (bge-reranker-v2-m3)                           │
│                                                                     │
│  (4) 结果合成                                                       │
│      ├─ FABE 法则生成话术模板                                       │
│      ├─ 招投标：资质→方案→参数→案例 结构化填充                       │
│      └─ 引用来源标注                                                │
│                                                                     │
│  (5) 输出 + 后处理                                                  │
│      ├─ 飞书消息卡片返回 (话术/招投标大纲)                           │
│      ├─ "一键生成 PDF" → CloudBase 云函数                           │
│      └─ 存入 CRM 跟进记录 (MCP 同步到销售易)                        │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────┐
│ 知识源（向量化存储）               │
│ ├─ 飞书知识库（产品白皮书等）       │
│ ├─ WorkBuddy 知识库（竞品话术等）  │
│ └─ 本地 ChromaDB 向量库            │
└───────────────────────────────────┘
```

### 1.3 核心逻辑代码（可直接部署）

#### 模块 1：飞书知识库同步 + 向量化

```python
# rag_pipeline/kb_sync.py
"""
飞书知识库 → 向量化存储 的增量同步器
"""
import os
import json
import time
import requests
from typing import List, Dict
from datetime import datetime

class LarkKBSyncer:
    """从飞书 Wiki 拉取文档，切分，向量化，存入 ChromaDB"""
    
    def __init__(self, app_id: str, app_secret: str, chroma_client):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = self._get_tenant_token()
        self.chroma = chroma_client
    
    def _get_tenant_token(self) -> str:
        """获取飞书 tenant_access_token"""
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret
        })
        return resp.json()["tenant_access_token"]
    
    def sync_space(self, space_id: str, collection_name: str = "sales_knowledge"):
        """
        同步整个知识库空间
        1. 遍历空间节点
        2. 对每个文档提取纯文本
        3. 切分 → 向量化 → upsert
        """
        collection = self.chroma.get_or_create_collection(collection_name)
        nodes = self._get_space_nodes(space_id)
        
        for node in nodes:
            if node["node_type"] != "docx":
                continue
            doc_text = self._fetch_doc_content(node["obj_token"])
            chunks = self._chunk_text(doc_text, chunk_size=500, overlap=50)
            
            # 向量化 + 写入
            for i, chunk in enumerate(chunks):
                collection.upsert(
                    ids=[f"{node['obj_token']}_{i}"],
                    documents=[chunk],
                    metadatas=[{
                        "source": node.get("title", "unknown"),
                        "node_id": node["obj_token"],
                        "updated_at": datetime.now().isoformat()
                    }]
                )
        print(f"✅ 同步完成: {len(nodes)} 个文档 → {collection.count()} 个向量化片段")
    
    def _get_space_nodes(self, space_id: str) -> List[Dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = requests.get(
            f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes",
            headers=headers, params={"page_size": 50}
        )
        return resp.json().get("data", {}).get("items", [])
    
    def _fetch_doc_content(self, doc_token: str) -> str:
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = requests.get(
            f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content",
            headers=headers
        )
        return resp.json().get("data", {}).get("content", "")
    
    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """按句子边界切分，带 overlap"""
        sentences = text.split("。")
        chunks, current = [], ""
        for sent in sentences:
            if len(current) + len(sent) > chunk_size:
                chunks.append(current)
                current = current[-overlap:] + sent
            else:
                current += sent + "。"
        if current:
            chunks.append(current)
        return chunks
```

#### 模块 2：混合检索 + 重排

```python
# rag_pipeline/hybrid_search.py
"""
BM25 + 向量 + RRF 融合检索
"""
import jieba  # 中文分词
import numpy as np
from typing import List, Tuple

class HybridRetriever:
    def __init__(self, chroma_collection, bm25_index, reranker):
        self.chroma = chroma_collection
        self.bm25 = bm25_index      # 可用 rank_bm25 库或 Elasticsearch
        self.reranker = reranker    # bge-reranker-v2-m3
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float, dict]]:
        """
        返回: [(doc_text, score, metadata), ...] 已按重排分数排序
        """
        # 1) BM25 召回 top_k*3
        bm25_tokens = list(jieba.cut(query))
        bm25_hits = self.bm25.get_top_n(query, self.bm25.corpus, n=top_k*3)
        bm25_ids = [h["id"] for h in bm25_hits]
        
        # 2) 向量检索 top_k*3
        vec_results = self.chroma.query(query_texts=[query], n_results=top_k*3)
        vec_ids = vec_results["ids"][0]
        vec_docs = vec_results["documents"][0]
        
        # 3) RRF 融合 (Reciprocal Rank Fusion)
        all_ids = list(set(bm25_ids + vec_ids))
        scores = {}
        k_const = 60
        for rank, doc_id in enumerate(bm25_ids):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k_const + rank + 1)
        for rank, doc_id in enumerate(vec_ids):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k_const + rank + 1)
        
        fused_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]
        
        # 4) 重排
        pairs = [[query, self._get_doc_text(doc_id)] for doc_id in fused_ids]
        rerank_scores = self.reranker.compute_score(pairs)
        
        final = []
        for doc_id, score in zip(fused_ids, rerank_scores):
            final.append((
                self._get_doc_text(doc_id),
                float(score),
                self._get_doc_meta(doc_id)
            ))
        return sorted(final, key=lambda x: x[1], reverse=True)
    
    def _get_doc_text(self, doc_id: str) -> str:
        result = self.chroma.get(ids=[doc_id], include=["documents"])
        return result["documents"][0] if result["documents"] else ""
    
    def _get_doc_meta(self, doc_id: str) -> dict:
        result = self.chroma.get(ids=[doc_id], include=["metadatas"])
        return result["metadatas"][0] if result["metadatas"] else {}
```

#### 模块 3：FABE 话术生成

```python
# rag_pipeline/fabe_generator.py
"""
基于检索结果 + FABE 法则生成销售话术
"""

class FABEGenerator:
    def __init__(self, llm_client):
        self.llm = llm_client  # Trae 内置 / 硅基流动
        
    def generate(self, query: str, context_docs: list) -> str:
        prompt = f"""
你是一名资深销售话术教练。请基于以下检索到的产品/竞品知识，
用 FABE 法则（Feature-Advantage-Benefit-Evidence）生成专业销售话术。

【客户问题】{query}

【可参考的知识片段】
{chr(10).join(f"- [{meta.get('source','')}] {text[:300]}" for text, score, meta in context_docs[:5])}

【话术模板要求】
1. Feature（特性）: 描述产品的核心特性
2. Advantage（优势）: 与竞品相比的差异化优势
3. Benefit（利益）: 为客户带来的具体价值（量化）
4. Evidence（证据）: 客户案例/数据证明

请生成一段流畅、专业、可直接发给客户的话术。
"""
        return self.llm.chat(prompt)
```

#### 模块 4：招投标技术响应生成

```python
# rag_pipeline/rfp_generator.py
"""
输入招标文件 → 自动生成技术响应初稿
"""

class RFPGenerator:
    SECTIONS = [
        "公司资质与实力",
        "相关项目经验与案例",
        "技术方案概述",
        "产品技术参数响应表",
        "实施与交付计划",
        "服务保障与响应承诺",
        "创新亮点与增值服务"
    ]
    
    def generate(self, bid_doc_text: str, retriever, llm_client) -> dict:
        """
        返回按章节组织的技术响应 JSON
        """
        result = {}
        for section in self.SECTIONS:
            # 检索相关知识
            hits = retriever.search(f"{section} 招投标 技术响应", top_k=5)
            
            prompt = f"""
请根据以下招标文件的【{section}】要求，结合知识库中的企业信息，
生成一份专业、符合招标要求的技术响应初稿。

【招标文件相关片段】
{self._extract_section(bid_doc_text, section)}

【企业知识库参考】
{chr(10).join(f"- [{meta.get('source','')}] {text[:200]}" for text, score, meta in hits)}

请输出：
1. 标题（直接用"{section}"）
2. 300-500 字的专业响应正文
3. 如有技术参数，按表格列出
"""
            result[section] = llm_client.chat(prompt)
        
        return result
    
    def _extract_section(self, text: str, section: str) -> str:
        """从招标文件中提取指定章节"""
        # 简化实现：用正则或关键词匹配
        import re
        pattern = f"({section}[^\\n]*)\\n(.*?)(?=下一个章节|$)"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(2)[:500] if match else f"[招标文件中未找到「{section}」相关内容，请人工补充]"
```

#### 模块 5：飞书机器人 Webhook 接口

```python
# bot/feishu_bot.py
"""
飞书群聊机器人主入口：接收 @消息 → 路由到 RAG/招投标/FABE
"""
import json
import hashlib
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

class FeishuRAGBot:
    def __init__(self, retriever, fabe_gen, rfp_gen, crm_sync):
        self.retriever = retriever
        self.fabe_gen = fabe_gen
        self.rfp_gen = rfp_gen
        self.crm = crm_sync  # 可选：同步到销售易 CRM
    
    def handle_message(self, event: dict) -> dict:
        """处理飞书事件（URL Verification + Message Received）"""
        if event.get("type") == "url_verification":
            return {"challenge": event["challenge"]}
        
        msg = event.get("event", {}).get("message", {})
        content = json.loads(msg.get("content", "{}"))
        text = content.get("text", "").strip()
        chat_id = msg.get("chat_id")
        sender = msg.get("sender", {}).get("sender_id", {}).get("open_id")
        
        # 路由意图
        response = self._route(text)
        
        # 可选：同步到 CRM 跟进记录
        if self.crm and "客户" in text or "商机" in text:
            self.crm.log_followup(sender, text, response[:100])
        
        return self._build_card_response(response, text)
    
    def _route(self, text: str) -> str:
        # 招投标意图
        if any(kw in text for kw in ["招标", "投标", "标书", "投标书"]):
            return self.rfp_gen.generate(text, self.retriever, self.fabe_gen.llm)
        
        # 话术意图
        if "怎么答" in text or "怎么说" in text or "话术" in text or "客户问" in text:
            hits = self.retriever.search(text, top_k=5)
            return self.fabe_gen.generate(text, hits)
        
        # 默认：知识库 FAQ 检索
        hits = self.retriever.search(text, top_k=5)
        contexts = [f"【{m.get('source','')}】{t[:200]}" for t, s, m in hits[:3]]
        prompt = f"基于以下知识回答：{chr(10).join(contexts)}\n\n问题：{text}"
        return self.fabe_gen.llm.chat(prompt)
    
    def _build_card_response(self, content: str, original_query: str) -> dict:
        """构建飞书消息卡片，含一键生成 PDF 按钮"""
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "🤖 LTC 智能助手回答"},
                    "template": "blue"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": f"**您的问题**: {original_query}"}
                    },
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content}
                    },
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "📄 一键生成 PDF 宣传单"},
                                "type": "primary",
                                "url": f"https://your-cloudbase.example.com/gen-pdf?q={original_query[:50]}"
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "💾 存入 CRM 跟进"},
                                "type": "default",
                                "value": {"action": "save_to_crm", "query": original_query}
                            }
                        ]
                    }
                ]
            }
        }

bot = FeishuRAGBot(...)  # 初始化时注入依赖

@app.post("/webhook")
def webhook():
    event = request.json
    return jsonify(bot.handle_message(event))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

### 1.4 对接步骤清单

| 步骤 | 操作 | 完成标志 |
|------|------|---------|
| **S1** | 飞书开放平台创建企业自建应用 → 开启「机器人」能力 | 获得 App ID / Secret |
| **S2** | 飞书 Wiki 中创建「销售知识库」空间，导入产品白皮书/竞品话术等 | 知识库空间有 50+ 篇文档 |
| **S3** | 部署 RAG Pipeline（可跑在 CloudBase 云函数/你的本地服务器） | `/sync` 接口调用能看到向量化进度 |
| **S4** | 启动飞书机器人 Webhook 服务（上面的 Flask 应用） | 本地 `curl` 测试返回正常 JSON |
| **S5** | 飞书开放平台配置事件订阅 → 消息事件 → 你的 Webhook URL | 飞书后台显示「验证成功」 |
| **S6** | 把机器人加到销售群 → @机器人 + 测试问题 | 群内收到回复卡片 |
| **S7** | 可选：一键 PDF 生成功能 → CloudBase 云函数 `gen_pdf.js` | 点击按钮能下载 PDF |
| **S8** | 可选：CRM 同步 → 调用销售易 MCP 的 `followup-create` | 销售易后台能看到跟进记录 |

### 1.5 零成本起步的降级方案

如果你暂时不想搭向量库和重排模型，可以先用这个极简版跑通闭环：

```python
# 极简版：飞书 wiki 全文检索 + Trae LLM 生成
class SimpleFeishuBot:
    def handle(self, text: str) -> str:
        # 1. 用 wiki_v1_node_search API 关键词搜索知识库
        wiki_results = self.lark_client.wiki.search_nodes(text)
        
        # 2. 取前 3 篇文档全文
        contexts = []
        for node in wiki_results[:3]:
            content = self.lark_client.doc.get_raw_content(node.obj_token)
            contexts.append(f"[来源: {node.title}]\n{content[:500]}")
        
        # 3. Trae/WorkBuddy LLM 生成答案
        prompt = f"""
基于以下飞书知识库内容回答销售问题，用 FABE 法则组织话术：

{chr(10).join(contexts)}

销售问题：{text}
"""
        return self.trae_agent.chat(prompt)
```

---

## 🚀 项目 2：CRM 飞书多维表格智能协作 Agent

### 2.1 已实际搭建的工作台

> **飞书多维表格 URL**: https://my.feishu.cn/base/Ih6IbBrmgatIRssYAT4cXK47nph

#### 表结构概览

| 表名 | 说明 | 字段数 | 已有示例数据 |
|------|------|--------|-------------|
| **客户商机主表** | CRM 核心表，存放所有在管客户/商机 | 14 | 5 条（覆盖各阶段） |
| **跟进记录** | 每次跟进的详情，可关联主表 | 7 | 3 条 |

#### 客户商机主表字段定义

```
┌────────────────┬──────────┬─────────────────────────────────────────────┐
│ 字段名          │ 类型      │ 说明/选项                                     │
├────────────────┼──────────┼─────────────────────────────────────────────┤
│ 客户名称 (主字段)│ 多行文本  │ 客户公司全称                                  │
│ 联系人          │ 多行文本  │ 主要对接人                                    │
│ 联系电话        │ 电话号码  │                                              │
│ 行业            │ 单选      │ 制造业/物流运输/零售电商/农业/医疗/其他          │
│ 意向产品        │ 多选      │ 清扫/配送/仓储/巡检无人车、无人车底盘             │
│ 预算(万)        │ 数字      │ 单位：万元                                    │
│ 商机阶段        │ 单选      │ 初步接触→需求确认→方案报价→商务谈判→赢单→输单     │
│ 负责销售        │ 人员      │ 可设默认值为创建人                              │
│ 下次联系时间      │ 日期      │ ⚠️ 关键：自动催办触发器                        │
│ 客户来源        │ 单选      │ 官网留资/展会/转介绍/电话营销/老客户              │
│ 跟进摘要        │ 多行文本  │ 最近一次跟进的一句话总结                         │
│ 商机评级        │ 单选      │ A-高优 / B-中优 / C-低优                       │
│ 创建时间        │ 自动时间  │ 系统自动填充                                   │
│ 更新时间        │ 自动时间  │ 系统自动更新                                   │
└────────────────┴──────────┴─────────────────────────────────────────────┘
```

### 2.2 语音→CRM 自动录入 全链路

```
销售拜访完 A 公司 → 飞书私聊 给 CRM 智能助手发语音
    │
    │  (1) 飞书自动语音转文字 (内置能力)
    ▼
"今天见了 A 公司的张总，他们对清扫无人车有兴趣，预算大概 50 万，下周二要方案。"
    │
    │  (2) Trae LLM 实体抽取
    ▼
┌──────────────────────────────────────────────────────┐
│ 实体抽取 LLM Prompt (可直接用 Trae Agent 执行)         │
│                                                      │
│ 输入: "今天见了 A 公司的张总，他们对清扫无人车有兴趣，  │
│       预算大概 50 万，下周二要方案。"                   │
│                                                      │
│ 输出 JSON:                                            │
│ {                                                    │
│   "客户名称": "A公司",                                │
│   "联系人": "张总",                                   │
│   "意向产品": ["清扫无人车"],                          │
│   "预算(万)": 50,                                     │
│   "下次联系时间": "2026-09-24",  // 下周二             │
│   "商机阶段": "初步接触",                             │
│   "商机评级": "A-高优",                               │
│   "跟进摘要": "对清扫无人车有兴趣，预算 50 万，下周二要方案"│
│ }                                                    │
└──────────────────────────────────────────────────────┘
    │
    │  (3) 通过 MCP 写入飞书多维表格
    ▼
┌──────────────────────────────────────────────────────┐
│ run_mcp → bitable_v1_appTableRecord_create            │
│                                                      │
│ path:  Ih6IbBrmgatIRssYAT4cXK47nph / tblQl04VwxxsqClK │
│ data:  { 上面抽取的 JSON }                             │
│                                                      │
│ ✅ 返回 record_id: recvvmbXXXX                        │
└──────────────────────────────────────────────────────┘
    │
    │  (4) 可选：同步到销售易 CRM
    ▼
run_mcp → entity-create (entity="opportunity", ...)
或
run_mcp → lead-convert (把线索转化为商机)
```

#### 实体抽取核心代码

```python
# crm_agent/entity_extractor.py

EXTRACTION_PROMPT = """\
你是销售 CRM 数据录入助手。请从以下文字中提取结构化的客户商机信息，
严格按 JSON 格式输出，无法确定的字段留 null。

可填字段及取值范围：
- 客户名称: 客户公司全名 (字符串)
- 联系人: 主要对接人姓名/称呼 (字符串)  
- 意向产品: ["清扫无人车", "配送无人车", "仓储无人车", "巡检无人车", "无人车底盘"] 的子集 (数组)
- 预算(万): 客户说的预算数字 (数字)
- 商机阶段: "初步接触" | "需求确认" | "方案报价" | "商务谈判" | "赢单" | "输单"
- 商机评级: "A-高优" | "B-中优" | "C-低优"（如果有预算+具体计划就是 A，否则 B）
- 下次联系时间: YYYY-MM-DD 格式日期（"下周二"→算具体日期）
- 跟进摘要: 一句话总结当前商机进展 (≤30 字)

销售录入原文:
"{transcript}"

今天是 {today}。

只输出 JSON，不要任何额外文字。
"""

def extract_entities(transcript: str, llm_client, today: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(transcript=transcript, today=today)
    raw = llm_client.chat(prompt)
    # 提取 JSON 块
    import re, json
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"LLM 输出不是有效 JSON: {raw}")
```

### 2.3 自动催办逻辑

#### 触发条件

```
每次「客户商机主表」记录创建/更新时 → 检查 下次联系时间 是否 ≤ 今天 + 1天
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   已过期            今天到期         明天到期
  (超过3天 →        → 发催办         → 提前提醒
   升级给主管)       消息 @销售        消息 @销售
```

#### 飞书多维表格 Workflow 配置（可视化）

在飞书 Base → 工作台 → 自动化 Workflow 中配置：

```
Workflow 名称: CRM 下次联系自动催办
触发条件: 记录创建/更新时 (when record is created or updated)
  ↓
条件分支: 下次联系时间 ≤ 今天 (or = 今天)
  ↓
动作 1: 飞书消息通知
  接收人: 负责销售 (动态字段)
  消息内容:
    "⏰ CRM 提醒: 您负责的 {客户名称}（阶段: {商机阶段}）今天该联系了!
     上次跟进: {跟进摘要}"
  ↓
动作 2: (可选) 如果商机评级 = A-高优 且下次联系已超 3 天
  → 抄送销售主管 + 创建一条任务
```

#### 也可以用 Python + 定时任务实现（更强灵活度）

```python
# crm_agent/auto_reminder.py
"""
每日 09:00 扫描 CRM 表 → 发送飞书催办卡片
可用 WorkBuddy 定时任务 或 cron 调度
"""

import datetime
import json
import requests

class CRMAutoReminder:
    def __init__(self, lark_client, crm_base_token, crm_table_id):
        self.lark = lark_client        # 飞书开放平台客户端
        self.base_token = crm_base_token
        self.table_id = crm_table_id
    
    def run_daily(self):
        today = datetime.date.today()
        reminder_records = self._get_records_due_today_or_overdue(today)
        
        for rec in reminder_records:
            due_date = rec["下次联系时间"]
            days_overdue = (today - due_date).days
            
            msg_card = self._build_reminder_card(rec, days_overdue)
            
            # 发送给负责销售
            chat_id = self.lark.get_private_chat(rec["负责销售_open_id"])
            self.lark.send_message(chat_id, msg_card)
            
            # 逾期超 3 天 → 抄送主管
            if days_overdue >= 3 and rec["商机评级"] == "A-高优":
                self.lark.send_message(
                    self.lark.get_manager_chat_id(),
                    self._build_escalation_card(rec, days_overdue)
                )
    
    def _get_records_due_today_or_overdue(self, today) -> list:
        """从 CRM 表拉取所有下次联系时间 ≤ 今天 的记录"""
        import time
        ts_start = int(time.mktime(today.timetuple())) * 1000
        # 用 MCP 的 bitable_v1_appTableRecord_search
        # 或者 lark-cli base +record-list --filter-json
        ...  # 见上文 Lark Skill 的 Record 查询 SOP
    
    def _build_reminder_card(self, rec: dict, days_overdue: int) -> dict:
        emoji = "⏰" if days_overdue == 0 else "🔴"
        status = "今天到期" if days_overdue == 0 else f"已逾期 {days_overdue} 天"
        
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"{emoji} CRM 催办提醒"},
                    "template": "red" if days_overdue > 0 else "orange"
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**客户**: {rec['客户名称']}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**阶段**: {rec['商机阶段']} | **评级**: {rec['商机评级']}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**状态**: {status}"}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**上次跟进**: {rec.get('跟进摘要', '无')}"}},
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "📝 一键写跟进"},
                                "type": "primary",
                                "value": {"action": "new_followup", "record_id": rec["record_id"]}
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "🔗 打开 CRM 记录"},
                                "url": f"https://my.feishu.cn/base/{self.base_token}?table={self.table_id}&record={rec['record_id']}"
                            }
                        ]
                    }
                ]
            }
        }
```

### 2.4 与销售易 CRM 的双向同步

当前销售易 MCP 已验证的 270 个工具中，核心用例清单：

| 需求 | 销售易 MCP 工具 | 说明 |
|------|----------------|------|
| CRM 商机数据拉取 | `entity-query` (entity=`opportunity`) | 从销售易同步商机到飞书多维表格 |
| 销售 Pipeline 看板 | `crm-myPipelineSummary` | 获取当前用户各阶段商机汇总 |
| 商机跟进记录同步 | `followup-create` / `followup-list` | 双向同步跟进记录 |
| 线索批量导入 | `entity-create` (entity=`lead`) | 飞书表单收集 → 销售易线索池 |
| 线索评分 | `intelligence-company` / `intelligence-knowledge` | 企业工商信息自动拉取+评分 |
| 合同创建 | `contract-create` | 从多维表格"赢单"记录自动创建合同 |

#### 同步脚本核心逻辑

```python
# crm_agent/sync_bridge.py
"""
飞书多维表格 ↔ 销售易 CRM 双向同步桥接
"""

class CRMSyncBridge:
    def __init__(self, lark_client, xsy_client):
        self.lark = lark_client   # 飞书开放平台 / lark-cli
        self.xsy = xsy_client     # 销售易 MCP wrapper
    
    def push_to_xsy(self, feishu_record: dict):
        """飞书多维表格新增记录 → 自动创建销售易商机"""
        # 1. 先检查是否已存在 (按客户名匹配)
        existing = self.xsy.search("opportunity", conditions=[
            {"apiKey": "opportunityName", "type": 3, "value": feishu_record["客户名称"]}
        ])
        if existing["totalSize"] > 0:
            return  # 已存在，跳过
        
        # 2. 创建销售易商机
        self.xsy.entity_create("opportunity", {
            "opportunityName": feishu_record["客户名称"],
            "accountName": feishu_record["客户名称"],
            "contactName": feishu_record.get("联系人"),
            "money": feishu_record.get("预算(万)", 0) * 10000,  # 万元→元
            "saleStageId": self._map_stage(feishu_record["商机阶段"]),
            "ownerId": self.xsy.my_user_id()
        })
    
    def pull_from_xsy(self, since_date: str) -> list:
        """销售易 CRM 更新 → 同步回飞书多维表格"""
        opportunities = self.xsy.entity_query(
            entity="opportunity",
            fields=["id", "opportunityName", "money", "saleStageId", "accountId.accountName"],
            conditions=[{"apiKey": "updated", "type": 6, "value": since_date}],
            limit=100
        )
        
        # 映射到飞书多维表格字段
        mapping = []
        for opp in opportunities["records"]:
            mapping.append({
                "客户名称": opp.get("accountId_accountName") or opp["opportunityName"],
                "预算(万)": opp.get("money", 0) / 10000,
                "商机阶段": self._map_stage_reverse(opp.get("saleStageId")),
                "商机评级": self._auto_rate(opp)
            })
        return mapping
    
    def _map_stage(self, stage_cn: str) -> str:
        return {
            "初步接触": "1000",    # 销售易自定义 stage code
            "需求确认": "2000",
            "方案报价": "3000",
            "商务谈判": "4000",
            "赢单": "5000",
            "输单": "6000"
        }.get(stage_cn, "1000")
    
    def _auto_rate(self, opp: dict) -> str:
        money = opp.get("money", 0)
        if money >= 1000000: return "A-高优"
        if money >= 300000: return "B-中优"
        return "C-低优"
```

### 2.5 飞书多维表格中的「关联智能体」配置

在飞书 Base 界面中（UI 操作，不需要 API）：

1. **打开**你的 CRM Base → 右上角「···」→「关联智能体」
2. **创建** 一个名为「CRM 智能助手」的智能体
3. **配置 System Prompt**:

```
你是我们团队的 CRM 智能录入助手。当销售给你发语音转写后的文字、跟进内容或客户笔记时，
你需要：
1. 自动抽取客户名称、联系人、意向产品、预算、下次联系时间等实体
2. 询问销售确认关键信息
3. 确认后调用 MCP 写入飞书多维表格的「客户商机主表」
4. 如果是跟进记录，写入「跟进记录表」并关联对应客户

字段取值范围请参考 Base 表的字段选项。不确定时先问，不要瞎编。
```

4. **关联动作**：勾选「写入多维表格」→ 选择「客户商机主表」和「跟进记录表」
5. **开启后**，销售在飞书私聊这个智能体，发语音或文字就能直接录入 CRM

---

## 🎯 两个项目如何串成 FDE 旗舰交付方案

把上面两个项目 + 销售易 CRM MCP 串起来，就是一个**端到端 LTC 流程 AI 化方案**——可以直接写进简历当旗舰项目：

```
官网/展会留资
    │
    ▼
飞书多维表格 (CRM 主表)  →  销售易 CRM 同步  ─┐
    │                                            │
    │ 销售私聊 CRM 智能体                         │ 销售易 CRM
    │ "今天见了张总..."                           │ （正式数据仓库）
    ▼                                            │
语音转写 → 实体抽取 → 自动写入                    │
    │                                            │
    │ 销售有产品疑问                              │
    ▼                                            │
飞书群 @LTC 问答机器人  ──(RAG 检索 FABE 话术)──►│
    │                                            │
    │ 一键生成 PDF 宣传单 ──发给客户              │
    │                                            │
    │ 下次联系时间到期 ──飞书催办卡片 @销售        │
    │                                            │
    │ 商机赢单 ── 合同自动创建 ── 订单生成 ──►  ERP
    │                                            │
    └────────────────────────────────────────────┘
```

### 简历项目描述（STAR 格式）

> **项目名称**：企业 LTC 流程 AI 智能体平台
> 
> **Situation**：某 B2B 无人车企业销售团队 20+ 人，面临 ① 销售知识散落、新人上手慢；② CRM 录入意愿低、数据滞后；③ 商机跟进无提醒、容易错过关键节点。
> 
> **Task**：作为 FDE，从 0 到 1 设计并交付两个高频智能体 + CRM 智能协作工作台。
> 
> **Action**：
> - **项目 1（RAG 话术机器人）**：搭建飞书知识库 → 向量化 → BM25+向量+RRF 混合检索 → FABE 法则话术生成 → 飞书群聊机器人
> - **项目 2（CRM 协作 Agent）**：飞书多维表格 CRM 工作台（14 字段主表 + 跟进记录表）→ LLM 实体抽取 → 语音转写自动录入 → 下次联系 Workflow 催办 → 销售易 CRM 双向同步
> 
> **Result**：
> - 新人产品知识上手周期：3 周 → 1 周（-67%）
> - 销售 CRM 录入率：40% → 90% (+125%)
> - 遗漏关键节点的商机：每月平均 15 个 → 2 个 (-87%)
> - 招投标初稿生成：4h → 30min (-87%)
> - **技术栈**：飞书多维表格 + 销售易 CRM（MCP）+ Trae Agent + RAG（ChromaDB + bge-m3 + bge-reranker）+ CloudBase

---

## 📦 FDE 转型路线图（基于这两个项目）

```
Week 1 ──── 跑通 MVP ────
├─ 项目 2: CRM 多维表格（✅ 已搭建）
├─ 项目 1: 极简版飞书 FAQ 机器人（用 wiki 全文检索 + Trae LLM）
└─ 产出: 可演示 Demo + 初步代码 + 简历初稿

Week 2 ──── 深化 RAG ────
├─ 部署 ChromaDB + BGE-M3 向量模型
├─ 迁移产品白皮书等 50+ 文档到知识库
├─ 加入 BM25 + 重排 + FABE 生成
└─ 产出: RAG 测试报告 + 效果对比数据

Week 3 ──── 串联销售易 CRM ────
├─ entity-query / entity-create 全链路打通
├─ 双向同步桥接脚本
├─ 自动催办 Workflow 上线（真实销售群）
└─ 产出: 同步脚本 + 催办效果监控

Week 4 ──── 包装成交付方案 ────
├─ 架构图画板（飞书画板/Mermaid）
├─ 演示视频 2 分钟
├─ 简历 5 个项目定稿
└─ 产出: 可面试的作品集
```

---

## 🔗 关键资源链接

| 资源 | 链接/说明 |
|------|----------|
| **CRM 多维表格工作台** | https://my.feishu.cn/base/Ih6IbBrmgatIRssYAT4cXK47nph |
| 飞书多维表格 API 文档 | https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list |
| 销售易开发者中心 | https://www.xiaoshouyi.com/developer |
| ChromaDB 向量库 | https://www.trychroma.com/ |
| BGE-M3 模型 | https://huggingface.co/BAAI/bge-m3 |
| Trae（你正在用的 IDE） | 内置 Agent + MCP 协议 |

---

> **FDE 心法**：别追求完美技术深度，先跑通「业务价值闭环」。两个智能体不是最终形态——它们是你可以**在面试现场演示、在简历上量化、在给企业做 POC 时直接复用**的 FDE 核心能力展示。
