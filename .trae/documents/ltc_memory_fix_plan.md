# CloudBase 免费版内存修复计划

## 目标
把内存从 ~225MB 压到 ~80MB，让 CloudBase 免费版（0.5核 128MB）能跑起来。

## 依赖大砍（12 → 7 包）

| 删掉 | 原因 | 替代方案 |
|------|------|----------|
| chromadb (~100MB) | 重 | 自写 `SimpleVectorStore`（numpy + json，~150 行） |
| scikit-learn (~50MB) | 重 | 自写 `simple_tfidf()`（numpy + jieba，~20 行） |
| rank-bm25 (~10MB) | 轻但可砍 | 自写 `simple_bm25()`（numpy，~30 行） |
| scipy（sklearn 依赖） | 连带砍 | 不需要 |

| 保留 | 用途 |
|------|------|
| fastapi + uvicorn + pydantic | Web 框架 |
| numpy | 向量计算（不可砍） |
| jieba | 中文分词 |
| requests | 飞书/LLM HTTP |
| python-multipart | 文件上传 |
| pypdf | PDF 解析 |

## app.py 改造

1. 删掉所有 `import chromadb`
2. 删掉所有 `import sklearn`
3. 新增 `SimpleVectorStore` 类（numpy 存 TF-IDF 矩阵 + JSON 持久化）
4. 新增 `simple_tfidf()` 函数（jieba 分词 + numpy 频率统计）
5. 新增 `simple_bm25()` 函数（numpy 实现）
6. 把 ChromaDB 初始化 → SimpleVectorStore 初始化
7. 把所有 `COL.add / COL.query / COL.get / COL.update / COL.delete` 替换

## 持久化方式变化

```
之前: ChromaDB SQLite 文件 + COS 挂载
现在: numpy .npy 向量文件 + JSON 元数据文件 + COS 挂载
文件结构:
  /mnt/chroma/
    vectors.npy        ← numpy 矩阵 (n_docs × n_features)
    metadata.json      ← [{id, source, text, strategy}, ...]
    vocab.json         ← TF-IDF 词表
```

## 风险评估

| 功能 | 影响 |
|------|------|
| 向量检索速度 | 50 条以内无差别，1000+ 比 chromadb 慢几秒（你目前远没到） |
| 元数据过滤 | 从 ChromaDB 原生过滤 → Python list filter（慢但够用） |
| 切片编辑 | PATCH /chunks/{id} → 重新算 TF-IDF + 覆盖 JSON（可行） |
| COS 挂载 | 不变，只是文件从 SQLite 变 .npy + .json |

## 步骤

1. 改 requirements.txt
2. 改 app.py（替换 chromadb + sklearn 为自写实现）
3. 本地验证语法 + 导入
4. 推 GitHub
5. 用户在 CloudBase 重建服务（COS 挂载保留）
