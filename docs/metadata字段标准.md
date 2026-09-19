# 标准 Metadata Schema

> 所有向量 store 的切片必须包含以下字段。
> 旧数据靠 `.get(field, None)` 向后兼容，**绝不能直接重构 metadata.json**。
>
> ⚠️ **2026-09-18 校准**：本文原写 `file_size` 为「字节」，与实现不符（实现按**字符数**）。
> 已修正为字符数并说明理由，详见文末《口径校准》。校准基线 commit `a6d90ad`。

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
| `file_size` | integer (**字符数**) | ✅ | **切片文本的字符数**（非字节数，见文末《口径校准》） |

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
        "file_size": len(text),          # ← 字符数，与 app.py add() 一致（勿改成 len(text.encode()))
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

## /kb 端点返回结构（2026-09-18 实测，非设计稿）

**顶层键**：`by_type` / `files` / `ids_by_source` / `sources` / `total_chunks` / `total_files`

```json
{
  "total_chunks": 6,
  "total_files": 4,
  "sources": { "product-manual.pdf": 1, "faq.md": 1 },
  "ids_by_source": { "product-manual.pdf": ["<uuid>"] },
  "files": {
    "product-manual.pdf": {
      "source": "product-manual.pdf",
      "file_type": ".pdf",
      "created_at": "2026-09-18T08:08:25.997937Z",
      "chunks": 1,
      "total_chars": 58
    }
  },
  "by_type": {
    ".pdf": [ { "source": "product-manual.pdf", "file_type": ".pdf",
                "created_at": "2026-09-18T08:08:25.997937Z",
                "chunks": 1, "total_chars": 58 } ],
    ".md":  [ { "source": "faq.md", "...": "..." } ]
  }
}
```

**两处与旧文档不同，务必注意**：

| 项 | 旧文档写法 | **实际返回** | 影响 |
|---|---|---|---|
| `files[*]` 字段 | `last_modified` + `file_size` | **`created_at` + `total_chars`**（`file_size` 按 source 累加后改名为 `total_chars`） | 前端按 `file_size` 取值会拿到 `undefined` |
| `by_type` 形状 | `{".pdf": 57}`（计数） | **`{".pdf": [文件对象, …]}`（数组）** | 前端当数字用会出错；计数请用 `arr.length` |

`created_at` 取该 source 下**最早**的一条（同一文件多次入库取首次时间）。

---

# 口径校准（2026-09-18）

| 项 | 旧文档 | **实际实现** | 证据 |
|---|---|---|---|
| `file_size` 单位 | 「字节」，示例 `len(text.encode("utf-8"))` | **字符数**，`len(text)` | `code/app.py:283` `"file_size": len(documents[i])`；`code/scripts/upgrade_metadata.py:89` 同口径并带注释 |
| `/kb` 聚合字段名 | `file_size` | **`total_chars`** | `code/app.py:991` `"total_chars": sz`、`:996` 累加 |

**为什么按字符数而不是字节数**：`/kb` 把每片的 `file_size` 累加成 `total_chars`（字段名本身就写明是 chars）。
若改成字节数，中文内容会凭空变大约 3 倍，且字段名与实际含义不符。**统一按字符数，字段名与语义才自洽。**

> ⚠️ 后续维护者注意：**不要把 `len(text)` 改成 `len(text.encode("utf-8"))`**，否则
> `/kb` 的 `total_chars` 语义被破坏，且与线上既有 207 条数据口径不一致。

