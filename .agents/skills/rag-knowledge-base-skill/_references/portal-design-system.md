# Portal 设计系统

> 商务风格配色 + 5-tab 信息架构 + 动态筛选。
> 0 处紫黑残留（验收门禁：grep -c 必须 = 0）。

---

## 配色规范

### 主色系

| Token | 色值 | 用途 |
|---|---|---|
| `--bg-body` | `#0b0f19` | 页面背景（近黑，商务感） |
| `--bg-card` | `#1f2937` | 卡片容器 |
| `--bg-header` | `#ffffff` | Header 背景（白底，干净） |
| `--text-primary` | `#f3f4f6` | 主文本（body 上） |
| `--text-secondary` | `#9ca3af` | 次级文本 / 时间戳 |
| `--text-header` | `#111827` | Header 上的文本（深色） |
| `--accent` | `#6366f1` | 靛蓝主色（按钮、链接、选中态） |
| `--accent-light` | `#818cf8` | 悬停态 |
| `--success` | `#10b981` | 绿色成功态（cache_hit） |
| `--warning` | `#f59e0b` | 橙色警告 |
| `--danger` | `#ef4444` | 红色错误态 |
| `--border` | `#374151` | 卡片边框 |

### 验收门禁

```bash
# 旧紫配色残留检查
grep -cE "#7c3aed|#8b5cf6|#a855f7|rgba\(124.*purple|rgb\(124" portal/index.html
# 必须 = 0
```

---

## 5-Tab 信息架构

```
┌──────────────────────────────────────────────────────┐
│ Header: 🤖 RAG 知识库管理台 · 版本 V4 · [API Key]     │
├────────┬────────┬────────────┬──────────┬────────────┤
│ 💬 问答 │ 📤 上传 │ 🔪 切片管理 │ 📁 文件管理 │ ⚙️ Prompt  │
└────────┴────────┴────────────┴──────────┴────────────┘
│                                                       │
│                  (tab 内容区)                           │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### Tab 1: 💬 问答

- 输入框 + 发送按钮
- 对话历史
- 每条回答右下角: `[⚡ cache_hit · 12ms]` 或 `[🤖 llm · 1.2s]`

### Tab 2: 📤 上传

- 文件上传（支持 .pdf / .md / .txt / .docx）
- 文本直贴
- 入库后显示切片数量

### Tab 3: 🔪 切片管理

- **动态筛选**: 来源下拉 + 策略下拉（从 `/chunks/options` 拿）
- 搜索框（按切片内容模糊搜）
- 分页（每页 20）
- 批量删除 checkbox
- 每条: 来源 tag + 策略 tag + 创建时间 + 200 字预览

### Tab 4: 📁 文件管理

- **按 type 分组**: `.pdf (5)` / `.md (3)` / `.txt (4)`
- **日期筛选**: 7 天 / 30 天 / 全部
- 每个文件: 名称 + 大小 + 最后入库时间 + 切片数
- 自定义: 删除 / 重命名 / 重新入库

### Tab 5: ⚙️ Prompt 设置

- 模型选择（zhipu:glm-4-flash / 其他）
- 温度 slider
- 重排开关
- context 长度 top-k

---

## 切片预览规范

| 元素 | 值 |
|---|---|
| 后端 text_preview 长度 | 200 字 |
| 前端 CSS | **不截断**（`word-break: break-word`） |
| 字符数统计 | 预览旁显示 `(245 chars)` |

**⚠️ 别让 CSS 二次截断** —— 本次踩过的坑：

```css
/* ❌ 错误 */
.chunk-item .preview {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;  /* 单行截断 */
}

/* ✅ 正确 */
.chunk-item .preview {
  word-break: break-word;
  white-space: pre-wrap;
}
```
