# 飞书机器人无反应 · Webhook 增强修复计划

## Repository Research

### 当前状态

| 项 | 状态 |
|---|---|
| CloudBase 云托管 | ✅ 运行中，公网 URL `https://ltc-rag-bot-315461-10-1450031534.sh.run.tcloudbase.com` |
| Docker + Python app | ✅ 启动正常，health 返回 ok |
| Portal 门户 | ✅ 完整 HTML 返回，/ingest /query / 智谱 LLM 全部 OK |
| Webhook endpoint | ✅ 手动模拟飞书消息 → 完整跑通（challenge + 消息处理 + LLM + 回复）|
| **飞书机器人实际回复** | ❌ 无反应 |

### 根因分析（按可能性排序）

#### 🥇 可能性 1：飞书开启了「加密密钥」（Encryption Key）但我们没解密

飞书事件订阅支持 AES 加密（可选）。如果用户在飞书后台开了加密密钥，飞书发来的 payload 格式是：
```json
{"encrypt": "encrypted_base64_string"}
```
**而不是** 直接的事件 JSON。我们当前代码直接 `payload.get("event", {})`，拿到空 dict → 静默返回 `{"ok": True}` → 不回复 → 飞书认为请求成功但我们没做事。

当前代码**完全没有加密解密逻辑**。

#### 🥈 可能性 2：飞书权限没配全 / 没发布版本

飞书自鉴应用需要：
- 权限：`im:message`（发送/接收消息）+ `im:chat`（获取群组）
- **发布版本**：加完权限后必须「创建版本 → 发布」才能生效
- 应用**已添加到目标群**（群设置 → 群机器人 → 添加，或直接 @ 时被邀请）

#### 🥉 可能性 3：Webhook 代码缺乏完整错误处理 + 可观测性

当前 webhook 问题：
1. 没有 try/except 包裹整个 handler → 任一步崩溃返回 500
2. 日志只有 `print` → CloudBase 进程日志可能丢失/不完整
3. 没有事件去重（event_id）→ 飞书重试可能导致重复回复
4. 没有验证 token 校验（token 字段）→ 基本防御缺失
5. `<at>` 标签正则可能在某些场景下匹配失败

---

## Files and Modules

- **`app.py`** — 唯一需要改的文件
  - 新增加密密钥解密函数 `_feishu_decrypt()`
  - 新增验证 token 校验 `_verify_feishu_token()`
  - 重写 `/webhook` handler：完整 try/except + 结构化日志 + 事件去重 + ping 处理

- **`.env` / 环境变量**（CloudBase 控制台改，不是代码）
  - 新增 `FEISHU_ENCRYPT_KEY`（可选）
  - 新增 `FEISHU_VERIFICATION_TOKEN`（可选）

---

## Implementation Steps

### Step 1: 飞书加密 + Token 校验基础设施

在 `app.py` 顶部（环境变量定义区域之后）新增：

```python
# ── 飞书事件安全配置 ──
FEISHU_ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "").strip()
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip()

def _feishu_decrypt(encrypt_str: str) -> dict:
    """飞书 AES-256-CBC 加密解密"""
    if not FEISHU_ENCRYPT_KEY:
        raise ValueError("FEISHU_ENCRYPT_KEY not configured")
    key = hashlib.sha256(FEISHU_ENCRYPT_KEY.encode()).digest()
    cipher = AES.new(key, AES.MODE_CBC, base64.b64decode(encrypt_str)[:16])
    # ... 解密逻辑
    return json.loads(decrypted)

def _is_duplicate_event(event_id: str) -> bool:
    """事件去重（内存 set，进程重启后清空）"""
    seen = _EVENT_IDS
    if event_id in seen:
        return True
    seen.add(event_id)
    if len(seen) > 10000:  # 内存保护
        seen.clear()
    return False
```

需要新增 import：`import hashlib, base64`，以及 `from Crypto.Cipher import AES`（或纯 Python 实现避免加依赖）。

**关键决策**：用纯 Python AES 实现 vs 加 `pycryptodome` 依赖。选 **纯 Python**（无额外依赖，镜像体积不变）。

### Step 2: 重写 /webhook handler

```python
@app.post("/webhook")
async def webhook(payload: dict, request: Request):
    """飞书事件回调 · 增强版"""
    # ── 1. 原始请求日志（调试用）──
    raw = await request.body()
    print(f"[webhook] ← 收到 {len(raw)} bytes")

    # ── 2. 解密（如果飞书发的是加密格式）──
    if "encrypt" in payload and FEISHU_ENCRYPT_KEY:
        try:
            payload = _feishu_decrypt(payload["encrypt"])
            print("[webhook] 解密成功")
        except Exception as e:
            print(f"[webhook] ❌ 解密失败: {e}")
            return {"ok": False, "error": "decrypt_failed"}

    # ── 3. Verification Token 校验 ──
    if FEISHU_VERIFICATION_TOKEN:
        token = payload.get("token", "")
        if token and token != FEISHU_VERIFICATION_TOKEN:
            print(f"[webhook] ❌ token mismatch: expected={FEISHU_VERIFICATION_TOKEN}, got={token}")
            return {"ok": False}

    # ── 4. URL Verification ──
    if "challenge" in payload and "type" in payload:
        print("[webhook] ← URL verification (challenge)")
        return {"challenge": payload["challenge"]}

    # ── 5. Ping / Heartbeat ──
    if payload.get("type") == "ping":
        return {"ok": True}

    # ── 6. 事件去重 ──
    event_id = payload.get("header", {}).get("event_id", "")
    if event_id and _is_duplicate_event(event_id):
        print(f"[webhook] ↻ 重复事件，跳过: {event_id}")
        return {"ok": True}

    # ── 7. 提取消息 ──
    evt = payload.get("event", {})
    msg = evt.get("message", {})
    
    # 调试日志：打印完整 payload（脱敏）
    print(f"[webhook] 📋 event_type={payload.get('header',{}).get('event_type')} "
          f"message_type={msg.get('message_type')} chat_type={msg.get('chat_type')}")

    if msg.get("message_type") != "text":
        print(f"[webhook] ⏭️ 非文本消息，跳过")
        return {"ok": True}

    try:
        text = json.loads(msg.get("content", "{}")).get("text", "").strip()
    except Exception:
        text = ""

    if not text:
        print("[webhook] ⏭️ 空文本")
        return {"ok": True}

    # ── 8. 去掉 @机器人 的 mention tag ──
    # 飞书格式: <at user_id="ou_xxx">机器人名</at> 或 <at id=ou_xxx></at>
    original_text = text
    text = re.sub(r'<at[^>]*>.*?</at>', '', text).strip()
    text = text.replace('@_user_1', '').strip()  # 有时用占位符
    
    if not text:
        print(f"[webhook] ⏭️ 只有 @mention，无实际内容 (raw='{original_text}')")
        return {"ok": True}

    if text.startswith("/"):
        print(f"[webhook] ⏭️ 命令消息，跳过: {text}")
        return {"ok": True}

    print(f"[webhook] 💬 处理消息: '{text[:80]}' (chat={msg.get('chat_id','')[:12]}...)")

    # ── 9. RAG 查询 ──
    try:
        result = await query({"question": text}, _=True)
        answer_text = result.body.decode() if hasattr(result, 'body') else json.dumps(result)
        try:
            reply = json.loads(answer_text).get("answer", str(answer_text))
        except Exception:
            reply = str(answer_text)
    except Exception as e:
        print(f"[webhook] ❌ RAG 查询失败: {e}")
        reply = f"⚠️ 查询出错: {str(e)[:200]}"

    # ── 10. 飞书回复 ──
    token = feishu_token()
    if not token:
        print("[webhook] ❌ 无 FEISHU_APP_ID/SECRET，跳过回复")
        return {"ok": True, "reply": reply[:100]}

    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "receive_id": msg.get("chat_id"),
                "msg_type": "text",
                "content": json.dumps({"text": reply[:4000]}),
            },
            timeout=15,
        )
        resp = r.json()
        if resp.get("code") == 0:
            print(f"[webhook] ✅ 飞书回复成功 (chat={msg.get('chat_id','')[:12]}...)")
        else:
            print(f"[webhook] ❌ 飞书回复失败: code={resp.get('code')} msg={resp.get('msg')}")
    except Exception as e:
        print(f"[webhook] ❌ 发送飞书异常: {e}")

    return {"ok": True}
```

### Step 3: 本地验证 + 推 GitHub + 重新部署

1. 本地启动 app.py，手动模拟加密/未加密 payload
2. push GitHub
3. `tcb cloudrun deploy` 重新部署（MinNum=1 保持常驻）
4. curl 测试 webhook 所有路径
5. 拉 CloudBase 运行日志，确认飞书请求真的到达

---

## Dependencies and Considerations

| 项 | 说明 |
|---|---|
| **加密实现** | 纯 Python AES-256-CBC，不加 `pycryptodome` 依赖，用 `hashlib` + `base64` 即可 |
| **内存** | 新增 `_EVENT_IDS` set（上限 10000）< 1MB，可忽略 |
| **CloudBase 运行日志** | 之前看不到 Python stdout 是因为容器还没跑起来，这次部署后会有完整日志 |
| **飞书环境变量** | 新增两个可选变量 `FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN`，不配也能跑 |

---

## Validation

1. ✅ 手动未加密 challenge → 返回 challenge
2. ✅ 手动未加密消息 → RAG + LLM + 回复逻辑完整
3. ✅ 加密 challenge（如果 user 配了 ENCRYPT_KEY）→ 解密后返回 challenge  
4. ✅ CloudBase 运行日志可见所有 `[webhook]` 前缀行
5. ✅ 飞书群 @机器人 → 机器人回复

---

## Risks

- **用户根本没开加密** → 新加密代码不触发，不影响。但我们加了大量调试日志，能看到飞书到底有没有推请求过来。
- **CloudBase 运行日志延迟** → 可能需要等 5-10 分钟才能看到最新日志，加了日志后耐心等
- **飞书权限/版本没发布** → 代码改好也没用，用户必须在飞书后台发布版本。这次改完代码后会给用户完整的飞书配置核对清单
