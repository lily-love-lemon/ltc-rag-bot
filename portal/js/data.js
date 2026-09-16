/* ===== FDE Hub · Project Data Registry =====
 * 每个项目一个条目；md 文档放在 docs/ 下，通过 fetch 加载。
 * 后期新增项目只需在 PROJECTS 数组追加一条 + 在 docs/ 放 md 文件。
 */
window.PROJECTS = [

  // ===== 项目 1 =====
  {
    id: "ltc-rag-bot",
    title: "LTC 销售话术与招投标 RAG 问答机器人",
    category: "ai-agent",          // ai-agent | crm-integration | prd | full-stack
    catLabel: "AI Agent / RAG",
    catClass: "card__cat--rag",
    status: "已上线",
    date: "2026-09",
    stack: ["Python", "ChromaDB", "BGE-M3", "bge-reranker", "飞书开放平台"],
    tags: ["RAG", "FABE 话术", "招投标", "飞书机器人"],
    summary:
      "把公司产品白皮书、竞品话术、合同范本做成 AI 超级大脑 — 销售在飞书群 @机器人问「客户问我们在零下30度怎么保证续航」，立即生成 FABE 话术；上传招标文件自动生成技术响应初稿。",
    highlights: [
      "BM25 + 向量 + RRF 混合检索 + bge-reranker 重排",
      "FABE 法则话术生成器",
      "7 章结构化招投标初稿",
      "一键生成 PDF 宣传单 + CRM 跟进自动同步",
    ],
    metrics: [
      ["新人上手周期", "3 周 → 1 周（-67%）"],
      ["招投标初稿", "4h → 30min（-87%）"],
      ["RAG 命中率", "3/20 → 18/20（+500%）"],
    ],
    docFile: "fde-project-implementation.md",   // docs/ 下的 md 文件名（也可共用同一个）
    docSection: "🚀 项目 1：LTC 销售话术与招投标 RAG 问答机器人",  // 可选：只展示 md 里的某一节
  },

  // ===== 项目 2 =====
  {
    id: "crm-feishu-agent",
    title: "CRM 飞书多维表格智能协作 Agent",
    category: "crm-integration",
    catLabel: "CRM × 办公",
    catClass: "card__cat--crm",
    status: "已搭建 ✅",
    date: "2026-09",
    stack: ["飞书多维表格", "销售易 CRM (MCP)", "Trae Agent", "飞书 Workflow"],
    tags: ["语音录入", "自动催办", "双向同步", "实体抽取"],
    summary:
      "销售不想填 CRM？发 30 秒语音给飞书智能助手：「今天见了 A 公司张总，预算 50 万，下周二要方案」→ 自动抽取实体、写入多维表格、同步销售易 CRM、下次联系到期自动发催办卡片。",
    highlights: [
      "飞书多维表格 CRM 工作台（已实际搭建）",
      "LLM 自动实体抽取（客户/联系人/预算/下一步）",
      "飞书 Workflow 下次联系自动催办 + 逾期 3 天升级主管",
      "销售易 CRM ↔ 飞书多维表格双向同步桥接",
    ],
    metrics: [
      ["销售 CRM 录入率", "40% → 90%（+125%）"],
      ["遗漏关键节点商机", "15 个/月 → 2 个/月（-87%）"],
      ["商机跟进覆盖率", "40% → 85%（+112%）"],
    ],
    docFile: "fde-project-implementation.md",
    docSection: "🚀 项目 2：CRM 飞书多维表格智能协作 Agent",
    liveUrl: "https://my.feishu.cn/base/Ih6IbBrmgatIRssYAT4cXK47nph",
  },

  // ===== 项目 3：PRD 模板 =====
  {
    id: "prd-template",
    title: "企业 AI Agent PRD 模板库",
    category: "prd",
    catLabel: "PRD / 方案",
    catClass: "card__cat--prd",
    status: "持续更新",
    date: "2026-09",
    stack: ["Markdown", "Trae Agent"],
    tags: ["PRD 模板", "FABE", "MVP 画布"],
    summary:
      "FDE 写 PRD 的统一模板集 — LTC 流程 AI 化、客服机器人、智能 BI、数据脱敏、合同审查等 50+ 场景结构化 PRD，复用即得。",
    highlights: [
      "标准化 FDE PRD 章节骨架",
      "业务场景拆解 × 技术选型 × 交付计划",
      "配 STAR 成果量化模板",
    ],
    metrics: [
      ["PRD 撰写时长", "3 天 → 半天"],
      ["评审通过率", "50% → 95%"],
    ],
    docFile: "fde-project-implementation.md",
    docSection: "📐 总体架构：两个智能体如何协同",
  },

  // ===== 占位：未来项目 =====
  {
    id: "placeholder-roadmap",
    title: "🗺️ FDE 转型路线图 & 学习体系",
    category: "prd",
    catLabel: "PRD / 方案",
    catClass: "card__cat--prd",
    status: "持续更新",
    date: "2026-09",
    stack: ["体系建设", "方法论"],
    tags: ["FDE 转型", "学习路径", "STAR 简历"],
    summary:
      "从 CRM 管理员到前沿部署工程师 FDE 的 4 周冲刺路线图 — Week 1 MVP 闭环 → Week 2 RAG 深化 → Week 3 销售易串联 → Week 4 简历 & POC 包装。",
    highlights: [
      "4 周学习节奏 + 每日目标",
      "简历项目 STAR 模板",
      "企业 POC 话术打磨清单",
    ],
    metrics: [
      ["转型周期", "3-6 月 → 4 周见成果"],
    ],
    docFile: "fde-project-implementation.md",
    docSection: "🎯 两个项目如何串成 FDE 旗舰交付方案",
  },
];

/* ===== Category Registry ===== */
window.CATEGORIES = {
  "ai-agent": {
    title: "🤖 AI Agent / RAG",
    desc: "企业知识库 → 向量化 → 混合检索 → 重排 → 话术/方案生成，从 0 到 1 的 RAG 实战。",
  },
  "crm-integration": {
    title: "🔗 CRM × 办公集成",
    desc: "销售易 CRM × 飞书多维表格 × 企微的多系统打通 — 对话即录入，群聊即管理。",
  },
  "prd": {
    title: "📋 PRD & 技术方案",
    desc: "标准化 FDE PRD 模板、技术架构设计、交付路线图 — 让你的方案有结构、有依据。",
  },
  "full-stack": {
    title: "🎨 全栈产品",
    desc: "CloudBase + React + 小程序 的端到端产品实现，企业级交付标准。",
  },
};
