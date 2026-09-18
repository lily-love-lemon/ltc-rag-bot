# 修复计划 · UI 还原紫色 Hero + 数据恢复

## 问题诊断（3 个独立问题）

### 问题 1: UI 被改丑了 — hero 区要求恢复紫色

**根因**: V4 把旧版紫色 hero 改成了白底+靛蓝青渐变 header

| 元素 | 旧版 (v3 baseline) | 当前 V4 | 用户反馈 |
|---|---|---|---|
| .header | `linear-gradient(135deg,#1e40af,#7c3aed)` 紫蓝大 banner · 居中 32px padding | 白底 #ffffff + 18px 小 header | ❌ 更难看了 |
| body | `#0f172a` 深蓝 | `#0b0f19` 近黑 | 接近，可保留 |
| 强调色 | `#7c3aed` 紫色 | `#6366f1` 靛蓝 | 要回紫色 |

**修复方向**: 把 header 样式 **完全恢复旧版 v3** 的紫色大 hero
- `.header { background: linear-gradient(135deg,#1e40af,#7c3aed); padding:32px 24px; text-align:center }`
- 所有强调色 `#6366f1` → `#7c3aed`（紫色）
- `.tag-purple` 保持 `#a78bfa`
- body 深色保留 `#0b0f19`（用户没说要改背景色）

### 问题 2: 内容全空 — 切片 0、向量库空、文件 0

**根因分析**:
- Dockerfile v3 和 v4 **完全相同** —— COS mount 没改
- 本地 `chroma_data/` 也是空的
- **最可能原因**: CloudBase COS 挂载路径 `/mnt/chroma` 在新版本部署后 **没有挂载到旧 bucket**，或者新容器 mount 了一个空目录
- 也可能: 旧数据在另一个 CloudBase 环境（截图 URL `ltc-rag-bot-315461-10-1450031534` 可能是旧部署的 URL）

**修复方向**:
1. 先 **回滚到 v3 baseline**（DeployId 015）—— 确认数据能不能回来
2. 如果回滚后数据恢复 → 说明是 COS mount 问题，需要检查 CloudBase 控制台的存储挂载配置
3. 如果回滚后数据还是空 → 说明 COS bucket 本身就是空的，需要重新上传文档

### 问题 3: "为什么内容是空的" — 要先回答用户

这是纯信息类请求，需要直接解释原因，然后修复。

---

## 修复步骤

### Step 1: 先回滚确认数据状态（最高优先级）

```bash
./rollback.sh
# = git checkout v3-20260918-smoke-green
# + tcb cloudrun rollback --versionName multi_tenant_1x7PV6ZTf8QEDK
# + sleep 30
```

等待 CloudBase rollback 完成 → 用截图里的 URL 刷新 Portal → 看数据回来没。

### Step 2: 恢复紫色 hero 配色（编辑 portal/index.html）

用 `git diff v3-20260918-smoke-green HEAD -- portal/index.html` 对比差异，**只把配色相关的改动**回退：

```bash
# 1. 恢复 header 样式为 v3 紫色大 hero
# 2. 把 #6366f1 (靛蓝) 全局替换回 #7c3aed (紫色)
# 3. 恢复 h1 大标题 + 副标题文案
# 4. 保持 V4 的功能增强（动态筛选 / 文件管理 tab / AnswerCache tag 等）
```

**必须小心**: 配色恢复，但 V4 的 **功能增强**（8 新端点、AnswerCache、cosine rerank、动态切片筛选）**全部保留**。只改 CSS 颜色和 hero 结构。

### Step 3: 如果回滚后数据没回来 → 检查 COS

如果 v3 baseline 回滚后数据还是 0：
- 说明 CloudBase 存储挂载有问题
- 用 `tcb storage` 检查 COS bucket 状态
- 手动确认 `/mnt/chroma/vectors.npy` 和 `metadata.json` 是否存在于 bucket
- 如果 bucket 是空的 → 需要重新上传文档并入库

### Step 4: 重新部署修复后的版本

```bash
git add portal/index.html
git commit -m "fix: 恢复紫色 hero 配色 (#7c3aed), 保留 V4 全部功能"
git tag v4.1-purple-hero
tcb cloudrun deploy -s ltc-rag-bot -e lily0625ai-d1gpwc1vw89141edd --force 2>&1 <<< $'\n'
```

### Step 5: 验证

1. Portal 紫色 hero 恢复（截图确认）
2. 数据恢复（切片数 > 0）
3. V4 功能全部保留（/chunks/options 动态筛选、AnswerCache 第三次命中、cosine rerank）
4. 冒烟测试全绿

---

## 改动范围

| 文件 | 改动 | 风险 |
|---|---|---|
| `portal/index.html` | CSS 配色从靛蓝回紫色；hero 结构恢复 v3 | **仅 CSS + HTML 骨架**，不改 JS 逻辑 |
| (可能) `rollback.sh` | 执行回滚 | CloudBase 操作 |
| app.py | **不改** —— V4 后端全部保留 | 无 |

---

## 验收标准

- [ ] hero 区: `linear-gradient(135deg,#1e40af,#7c3aed)` 紫蓝渐变恢复
- [ ] 切片数据恢复 > 0
- [ ] `grep -c "#7c3aed" portal/index.html` > 5（紫色主色）
- [ ] `grep -c "#6366f1" portal/index.html` = 0（靛蓝清除）
- [ ] AnswerCache 功能仍工作（第 3 次 cache_hit=True）
- [ ] 切片管理动态筛选仍工作
- [ ] `/health` 正常返回
