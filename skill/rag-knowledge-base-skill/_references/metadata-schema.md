# 标准 Metadata Schema

> 所有向量 store 的切片必须包含以下字段。
> 旧数据靠 `.get(field, None)` 向后兼容，**绝不能直接重构 metadata.json**。

---

## 字段定义

| 字段 | 类型 | 自动注入 | 说明 |
|---|---|---|---|
| `id` | string (UUID4) | ✅ | 切片唯一标识 |
| `text` | string | ❌ | 切片文本内容 |
| `source` | string | ✅ | 来源文件名（如 `product-manual.pdf`） |
| `strategy` | string | ✅ | 切片策略（默认 `default`） |
| `created_at` | string (ISO 8601) | ✅ | 切片入库时间 |
| `file_type` | string (扩展名) | ✅ | 文件类型（从 source 推断，如 `.pdf`） |
| `file_size` | integer (字节) | ✅ | 原文件大小（或切片文本长度） |

---

## 自动注入代码（Python）

```python
import uuid, os
from datetime import datetime, timezone

def _enrich_metadata(self, text: str, source: str, strategy: str, extra: dict = None) -> dict:
    """自动注入标准 metadata 字段"""
    m = {
        "id": str(uuid.uuid4()),
        "text": text,
        "source": source or "unknown",
        "strategy": strategy or "default",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_type": os.path.splitext(source)[1].lower() if source else "",
        "file_size": len(text.encode("utf-8")),
    }
    if extra:
        m.update(extra)
    return m
```

---

## 向后兼容读取

```python
# 旧 metadata 可能没有 created_at 等字段
def _normalize_metadata(self, raw: dict) -> dict:
    return {
        "id": raw.get("id", str(uuid.uuid4())),
        "text": raw.get("text", ""),
        "source": raw.get("source", "unknown"),
        "strategy": raw.get("strategy", "default"),
        "created_at": raw.get("created_at", None),     # 旧数据为 None
        "file_type": raw.get("file_type", ""),          # 旧数据为 ""
        "file_size": raw.get("file_size", 0),           # 旧数据为 0
    }
```

---

## /kb 端点返回结构示例

```json
{
  "total_chunks": 150,
  "total_files": 12,
  "files": {
    "product-manual.pdf": { "chunks": 45, "last_modified": "2026-09-18T12:00:00Z", "file_type": ".pdf", "file_size": 256000 },
    "faq.md": { "chunks": 12, "last_modified": "2026-09-17T08:00:00Z", "file_type": ".md", "file_size": 8420 }
  },
  "by_type": {
    ".pdf": 57,
    ".md": 45,
    ".txt": 48
  }
}
```
