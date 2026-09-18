# FDE · RAG 知识库构建 Skill

> Full-stack Delivery Engineering: 从 0 到 1 落地生产级 RAG 知识库系统的可执行协议。
> **22 条真实踩坑 + 4 阶段协议 + 代码模板 + 冒烟脚本**。

## 快速开始

### 1. 安装到本地

```bash
# 方式 A: 从 GitHub 克隆（推荐）
git clone https://github.com/<your-org>/rag-knowledge-base-skill.git ~/.trae/skills/rag-knowledge-base-skill

# 方式 B: 一键安装脚本
curl -sSL https://raw.githubusercontent.com/<your-org>/rag-knowledge-base-skill/main/install.sh | bash
```

### 2. Phase 0: 基线固化（必做）

```bash
cd <your-project>
./.agents/skills/rag-knowledge-base-skill/_commands/init-baseline.sh baseline-v1
```

### 3. 后续阶段

按 `SKILL.md` 的 Phase A → B → C 推进。每阶段有门禁检查。

---

## 目录结构

```
rag-knowledge-base-skill/
├── SKILL.md                          # 主入口（协议 + 路由）
├── _references/                      # 8 份参考文档
│   ├── pitfall-log.md                # ★ 22 条完整踩坑清单
│   ├── cloudbase-free-tier.md        # CloudBase 免费版限制 & workaround
│   ├── metadata-schema.md            # 标准 metadata 字段
│   ├── rerank-architecture.md        # cosine rerank 实现
│   ├── answer-cache-design.md        # AnswerCache 设计
│   ├── token-saving-playbook.md      # 7 种省 token 手法
│   ├── verification-checklist.md     # 13 项冒烟验收
│   └── portal-design-system.md       # Portal 配色 + 交互规范
├── _templates/                       # 代码起点
│   └── Dockerfile
├── _commands/                        # 可执行脚本
│   ├── init-baseline.sh              # Phase 0 基线固化
│   ├── deploy.sh                     # CloudBase 部署
│   └── smoke-test.sh                 # 13 项冒烟验收
├── _deps/
│   └── requirements.min.txt          # 最小依赖（< 50MB 内存）
├── install.sh                        # 一键安装
└── README.md                         # 本文件
```

## 适用场景

- ✅ CloudBase 免费版（0.5核 128MB）AI 应用
- ✅ FastAPI + 向量存储 + Portal 三件套
- ✅ 飞书 / 企微机器人 + RAG 问答
- ✅ 省 token / 缓存高频问题 / 重排提升精度
- ✅ 知识切片管理 / 文件管理系统

## 版本

| 版本 | 日期 | 来源 |
|---|---|---|
| v1.0.0 | 2026-09-18 | 从 LTC RAG Bot V4 项目沉淀 |

## License

MIT
