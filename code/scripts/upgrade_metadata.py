#!/usr/bin/env python3
"""
LTC RAG Bot · 旧 metadata 增强字段补齐脚本（幂等）
==================================================
背景：线上 207 条切片入库于 V3 时期，metadata 只有 4 字段
      (id / text / source / strategy)，缺 created_at / file_type / file_size。
      导致 /kb 的 by_type 分组只剩 ['.inline']、文件管理页无法按类型/时间归类。

本脚本只补字段，不改 text、不动 vectors.npy、不改 vocab.json。
可重复执行：第二次运行必须报告 0 处变更。

用法：
  python scripts/upgrade_metadata.py --dry-run          # 只看要改什么，不落盘
  python scripts/upgrade_metadata.py                    # 备份 + 落盘
  python scripts/upgrade_metadata.py --path /mnt/chroma # 指定数据目录（默认同 app.py 的解析逻辑）

安全：
  - 落盘前自动备份 metadata.json → metadata.json.bak-<UTC 时间戳>
  - 原子写入（临时文件 + rename），避免写坏
  - 字段只增不改，已有值的记录跳过
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_SUFFIXES = {".pdf", ".md", ".txt", ".docx", ".doc", ".csv", ".json"}


def resolve_data_dir(explicit: str | None) -> Path:
    """与 app.py 的 CHROMA_PATH 解析逻辑保持一致"""
    if explicit:
        return Path(explicit)
    env = os.environ.get("CHROMA_PATH")
    if env:
        return Path(env)
    if os.path.isdir("/mnt/chroma"):
        return Path("/mnt/chroma")
    return Path(__file__).resolve().parent.parent / "chroma_data"


def infer_file_type(source: str) -> str:
    """从来源文件名推断扩展名；无法推断时归到 .inline（粘贴文本）"""
    if not source or source in ("unknown", "inline"):
        return ".inline"
    suffix = Path(source).suffix.lower()
    return suffix if suffix else ".inline"


def sha256_of(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def upgrade(records: list[dict], fallback_ts: str) -> tuple[list[dict], dict]:
    """返回 (新记录列表, 统计)。纯函数，便于测试。"""
    stat = {
        "total": len(records),
        "added_created_at": 0,
        "added_file_type": 0,
        "added_file_size": 0,
        "already_complete": 0,
        "samples": [],
    }
    out = []
    for idx, md in enumerate(records):
        rec = dict(md)  # 浅拷贝，不原地改
        changed = False

        if not rec.get("created_at"):
            rec["created_at"] = fallback_ts
            # 诚实标注：老数据没有真实入库时间，此值是推断出来的
            rec["created_at_inferred"] = True
            stat["added_created_at"] += 1
            changed = True

        if not rec.get("file_type"):
            rec["file_type"] = infer_file_type(rec.get("source", ""))
            stat["added_file_type"] += 1
            changed = True

        if not rec.get("file_size"):
            # 与 app.py add() 保持一致：按字符数计（不是字节数）
            rec["file_size"] = len(rec.get("text", ""))
            stat["added_file_size"] += 1
            changed = True

        if changed and len(stat["samples"]) < 3:
            stat["samples"].append({
                "index": idx,
                "source": rec.get("source"),
                "file_type": rec["file_type"],
                "file_size": rec["file_size"],
            })
        if not changed:
            stat["already_complete"] += 1
        out.append(rec)
    return out, stat


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐旧 metadata 的增强字段（幂等）")
    ap.add_argument("--path", help="数据目录（含 metadata.json），默认同 app.py")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘")
    ap.add_argument("--created-at", help="指定 created_at 值（ISO 8601），默认取 metadata.json 的 mtime")
    args = ap.parse_args()

    data_dir = resolve_data_dir(args.path)
    meta_file = data_dir / "metadata.json"
    vec_file = data_dir / "vectors.npy"

    print("=" * 62)
    print(" LTC RAG Bot · metadata 增强字段补齐")
    print("=" * 62)
    print(f" 数据目录 : {data_dir}")
    print(f" metadata : {meta_file}  {'(存在)' if meta_file.exists() else '(不存在)'}")
    print(f" vectors  : {vec_file}  {'(存在)' if vec_file.exists() else '(不存在)'}")
    print(f" 模式     : {'DRY-RUN（不落盘）' if args.dry_run else '实际写入'}")

    if not meta_file.exists():
        print("\n❌ metadata.json 不存在，无法执行。")
        print("   线上数据请先下载：")
        print("   tcb storage download metadata.json metadata.json -e lily0625ai-d1gpwc1vw89141edd")
        return 1

    try:
        records = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"\n❌ 解析 metadata.json 失败: {e}")
        return 1

    if not isinstance(records, list):
        print("\n❌ metadata.json 顶层不是数组，结构异常，中止。")
        return 1

    # 老数据没有真实入库时间 → 用 metadata.json 自身 mtime 作为最接近的代理值
    if args.created_at:
        fallback_ts = args.created_at
    else:
        mtime = datetime.fromtimestamp(meta_file.stat().st_mtime, tz=timezone.utc)
        fallback_ts = mtime.isoformat().replace("+00:00", "Z")

    print(f" 推断时间戳: {fallback_ts}  (created_at_inferred=true)")
    print()

    vec_before = sha256_of(vec_file)
    new_records, stat = upgrade(records, fallback_ts)

    print("─" * 62)
    print(" 变更统计")
    print("─" * 62)
    print(f"  记录总数            : {stat['total']}")
    print(f"  补 created_at       : {stat['added_created_at']}")
    print(f"  补 file_type        : {stat['added_file_type']}")
    print(f"  补 file_size        : {stat['added_file_size']}")
    print(f"  本身已完整（跳过）   : {stat['already_complete']}")
    for s in stat["samples"]:
        print(f"    · [{s['index']}] {s['source']} → {s['file_type']} / {s['file_size']} 字符")

    changed = stat["total"] - stat["already_complete"]
    if changed == 0:
        print("\n✅ 无需变更（已是最新 schema），未落盘。")
        return 0

    if args.dry_run:
        print(f"\n🔍 DRY-RUN：将变更 {changed} 条，未落盘。去掉 --dry-run 执行。")
        return 0

    # ── 备份 ──
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = meta_file.with_name(f"metadata.json.bak-{ts}")
    shutil.copy2(meta_file, backup)
    print(f"\n📦 已备份: {backup}")

    # ── 原子写入 ──
    tmp = meta_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new_records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(meta_file)
    print(f"💾 已写入: {meta_file}")

    # ── 断言：向量文件未被触碰 ──
    vec_after = sha256_of(vec_file)
    if vec_before != vec_after:
        print("⚠️  警告: vectors.npy 发生了变化（本不应发生），请从备份回滚！")
        return 2
    print("✅ 校验: vectors.npy 未变更（本次只补字段，向量无需重算）")

    # ── 复查幂等 ──
    re_records = json.loads(meta_file.read_text(encoding="utf-8"))
    _, stat2 = upgrade(re_records, fallback_ts)
    ok = (stat2["total"] - stat2["already_complete"]) == 0
    print(f"✅ 幂等复查: 二次运行变更 {stat2['total'] - stat2['already_complete']} 条 "
          f"{'（通过）' if ok else '（不通过，请检查）'}")

    print("\n下一步：重启服务后 /kb 的 by_type 应出现真实类型分组。")
    print("回滚：cp " + str(backup) + " " + str(meta_file))
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
