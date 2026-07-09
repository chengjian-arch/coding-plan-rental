"""
Coding Plan 激活码租用系统 - API 服务
流程: 管理员生成激活码 → 顾客自助激活 → API代理 → 过期作废
"""

import time, random, asyncio, hashlib, uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import httpx

from database import (
    init_db, get_plan, update_plan,
    create_tenant, get_tenants, get_tenant, update_tenant,
    create_session, activate_session, get_session_by_code,
    end_expired_sessions, end_session_admin, validate_session_key,
    log_call, get_usage, get_sessions, get_logs, get_stats,
    get_providers, get_provider, create_provider, update_provider, delete_provider,
    find_provider_for_model, get_all_models,
)

app = FastAPI(title="Coding Plan 激活码租用", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

USER_AGENTS = ["Claude-Code/1.0","CodeBuddy/2.1","Cline/3.0","Cursor/1.5","OpenClaw/2.0","KiloCode/1.2"]
_last_req = 0

# ==================== 管理员认证 ====================
ADMIN_USER = "chengjian"
ADMIN_PASS = "Chengjian1992@"
_admin_tokens = {}  # {token: expire_time}

def _check_admin(authorization: str = Header(None, alias="Authorization")):
    """验证管理员token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "请先登录")
    token = authorization.replace("Bearer ", "")
    expire = _admin_tokens.get(token)
    if not expire or time.time() > expire:
        raise HTTPException(401, "登录已过期，请重新登录")
    return True

# ==================== 模型 ====================

class CreateCodeRequest(BaseModel):
    duration_minutes: int = Field(default=300, ge=30, le=480, description="时长(分钟)")
    price_per_hour: float = Field(default=0.6, description="时租单价")
    price_per_request: float = Field(default=0.002, description="按次单价")
    tenant_id: int = Field(default=0)
    notes: str = ''

class ActivateRequest(BaseModel):
    code: str = Field(..., description="激活码，如 CP8A3F")

class PlanUpdate(BaseModel):
    monthly_cost: float = None
    price_per_hour: float = None
    price_per_request: float = None
    cooldown_minutes: int = None
    min_minutes: int = None
    max_hours: int = None
    safety_pct: float = None
    coding_plan_api_key: str = None
    public_domain: str = None

class TenantCreate(BaseModel):
    name: str
    contact: str = ''


# ==================== 启动 ====================

@app.on_event("startup")
async def startup():
    init_db()


# ==================== 管理员登录 ====================

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/admin/login")
async def admin_login(data: LoginRequest):
    if data.username == ADMIN_USER and data.password == ADMIN_PASS:
        token = hashlib.sha256(f"{uuid.uuid4()}{time.time()}".encode()).hexdigest()[:32]
        _admin_tokens[token] = time.time() + 86400  # 24小时有效
        return {"token": token, "expires_in": 86400}
    raise HTTPException(401, "用户名或密码错误")

@app.post("/api/admin/logout")
async def admin_logout(authorization: str = Header(None, alias="Authorization")):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
        _admin_tokens.pop(token, None)
    return {"ok": True}


# ==================== 管理员 API（需要登录） ====================

@app.get("/api/admin/stats")
async def admin_stats(_auth = Depends(_check_admin)):
    return get_stats()

@app.get("/api/admin/plan")
async def admin_plan(_auth = Depends(_check_admin)):
    return get_plan()

@app.put("/api/admin/plan")
async def admin_plan_update(data: PlanUpdate, _auth = Depends(_check_admin)):
    # 兼容 Pydantic v1/v2
    try:
        kw = {k:v for k,v in data.model_dump().items() if v is not None}
    except AttributeError:
        kw = {k:v for k,v in data.dict().items() if v is not None}
    update_plan(**kw)
    return {"ok":True,"updated":kw}

@app.get("/api/admin/tenants")
async def admin_tenants(_auth = Depends(_check_admin)):
    return get_tenants()

@app.post("/api/admin/tenants")
async def admin_tenant_add(data: TenantCreate, _auth = Depends(_check_admin)):
    tid = create_tenant(data.name, data.contact)
    return {"id": tid, "name": data.name}

@app.put("/api/admin/tenants/{tid}")
async def admin_tenant_edit(tid: int, data: TenantCreate, _auth = Depends(_check_admin)):
    update_tenant(tid, name=data.name, contact=data.contact)
    return {"ok": True}

@app.post("/api/admin/tenants/{tid}/toggle")
async def admin_tenant_toggle(tid: int, _auth = Depends(_check_admin)):
    t = get_tenant(tid)
    if not t: raise HTTPException(404)
    new = 'suspended' if t['status']=='active' else 'active'
    update_tenant(tid, status=new)
    return {"status":new}

@app.get("/api/admin/sessions")
async def admin_sessions(limit: int=50, _auth = Depends(_check_admin)):
    return get_sessions(limit)

@app.get("/api/admin/logs")
async def admin_logs(session_id: int=None, limit: int=100, _auth = Depends(_check_admin)):
    return get_logs(session_id, limit)


class EndSessionRequest(BaseModel):
    session_id: int

@app.post("/api/admin/end-session")
async def admin_end_session(data: EndSessionRequest, _auth = Depends(_check_admin)):
    """管理员强制结束会话"""
    result = end_session_admin(data.session_id)
    if not result:
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "session": result}


# ==================== API 供应商管理 ====================

class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ''
    models: list = []
    is_default: bool = False

class ProviderUpdate(BaseModel):
    name: str = None
    base_url: str = None
    api_key: str = None
    models: list = None
    is_default: bool = None
    status: str = None

@app.get("/api/admin/providers")
async def admin_providers(_auth = Depends(_check_admin)):
    providers = get_providers()
    # 安全处理: 不暴露完整 api_key
    for p in providers:
        key = p.get('api_key','')
        p['api_key_masked'] = key[:8] + '****' + key[-4:] if len(key) > 12 else ('****' if key else '')
    return providers

@app.post("/api/admin/providers")
async def admin_provider_add(data: ProviderCreate, _auth = Depends(_check_admin)):
    pid = create_provider(data.name, data.base_url, data.api_key, data.models, data.is_default)
    return {"id": pid, "name": data.name}

@app.put("/api/admin/providers/{pid}")
async def admin_provider_edit(pid: int, data: ProviderUpdate, _auth = Depends(_check_admin)):
    try:
        kw = {k:v for k,v in data.model_dump().items() if v is not None}
    except AttributeError:
        kw = {k:v for k,v in data.dict().items() if v is not None}
    if not get_provider(pid):
        raise HTTPException(404, "供应商不存在")
    update_provider(pid, **kw)
    return {"ok": True}

@app.delete("/api/admin/providers/{pid}")
async def admin_provider_delete(pid: int, _auth = Depends(_check_admin)):
    ok = delete_provider(pid)
    if not ok:
        raise HTTPException(409, "不能删除默认供应商")
    return {"ok": True}


# ==================== 核心：生成激活码 ====================

@app.post("/api/admin/create-code")
async def admin_create_code(data: CreateCodeRequest, _auth = Depends(_check_admin)):
    """管理员生成激活码，拿给顾客"""
    try:
        result = create_session(
            data.duration_minutes,
            data.price_per_hour,
            data.price_per_request,
            data.tenant_id,
            data.notes
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    # 格式化显示
    h = data.duration_minutes // 60
    m = data.duration_minutes % 60
    dur_str = f"{h}小时" if m==0 else f"{h}小时{m}分钟" if h>0 else f"{m}分钟"

    plan = get_plan()
    domain = plan.get('public_domain','') or '你的服务器IP'
    est_requests = int(data.duration_minutes / 60 * 25)
    est_total = round(result['base_cost'] + est_requests * data.price_per_request, 2)
    return {
        **result,
        'duration_display': dur_str,
        'est_requests': est_requests,
        'est_total': est_total,
        'share_text': f"【Coding Plan 租用】激活码: {result['activation_code']}\n时长: {dur_str}\n费用: ¥{est_total} (基础 ¥{result['base_cost']} + ¥{data.price_per_request}/次)\n访问: http://{domain} 输入激活码即可使用",
    }


# ==================== 自助门户 API（顾客用） ====================

@app.post("/api/portal/activate")
async def portal_activate(data: ActivateRequest, request: Request):
    """顾客输入激活码，获取API凭证并开始计时"""
    try:
        info = activate_session(data.code.upper().strip())
    except ValueError as e:
        raise HTTPException(400, str(e))

    plan = get_plan()
    domain = plan.get('public_domain','') or _get_domain(request)

    return {
        **info,
        'api_endpoint': f'http://{domain}/v1/chat/completions',
        'api_key': info.get('session_key'),
        'usage_instruction': f'在 AI 工具中配置:\nBase URL: http://{domain}/v1\nAPI Key: ' + (info.get('session_key','')),
    }


@app.get("/api/portal/status")
async def portal_status(code: str, request: Request):
    """顾客随时查询剩余时间和调用次数"""
    end_expired_sessions()
    session = get_session_by_code(code.upper().strip())
    if not session:
        raise HTTPException(404, "激活码无效")

    if session['status'] in ('expired', 'cancelled'):
        return {
            **session,
            'expired': True,
            'message': '该激活码已过期，如需续租请联系管理员',
        }

    plan = get_plan()
    domain = plan.get('public_domain','') or _get_domain(request)
    return {
        **session,
        'expired': False,
        'api_endpoint': f'http://{domain}/v1/chat/completions',
    }


@app.get("/api/portal/models")
async def portal_models():
    """返回可用模型列表（汇总所有供应商）"""
    return {"models": get_all_models()}


# ==================== API 代理 ====================

@app.get("/v1/models")
async def proxy_models():
    """OpenAI 兼容: 返回可用模型列表"""
    models = get_all_models()
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "owned_by": m.get("provider",""), "desc": m.get("desc","")}
            for m in models
        ]
    }

@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    """OpenAI 兼容代理，用 session_key 鉴权，自动路由到对应供应商"""
    auth = request.headers.get("Authorization","")
    session_key = auth.replace("Bearer ","")

    if not session_key:
        raise HTTPException(401, "缺少 Authorization: Bearer <你的API Key>")

    session = validate_session_key(session_key)
    if not session:
        raise HTTPException(403, "API Key 已失效，租用时间已到。请在门户页面输入激活码查看状态。")

    # 请求间隔
    global _last_req
    elapsed = time.time() - _last_req
    if elapsed < 0.5:
        await asyncio.sleep(0.5 - elapsed + random.uniform(0, 0.3))
    _last_req = time.time()

    try:
        body = await request.json()
    except:
        raise HTTPException(400, "请求体解析失败")

    model = body.get("model","tc-code-latest")
    est = min(max(len(body.get("messages",[])), 1), 5)

    # 根据模型名找到对应供应商
    provider = find_provider_for_model(model)
    if not provider:
        raise HTTPException(503, "没有可用的 API 供应商，请联系管理员")

    provider_key = provider.get('api_key','')
    if not provider_key:
        raise HTTPException(500, f"供应商「{provider['name']}」尚未配置 API Key")

    upstream_url = provider['base_url'].rstrip('/') + '/chat/completions'

    headers = {
        "Authorization": f"Bearer {provider_key}",
        "Content-Type": "application/json",
        "User-Agent": random.choice(USER_AGENTS),
    }

    try:
        if body.get("stream"):
            async def stream():
                async with httpx.AsyncClient(timeout=120) as client:
                    try:
                        async with client.stream("POST",
                            upstream_url,
                            headers=headers, json=body, timeout=120
                        ) as resp:
                            async for chunk in resp.aiter_bytes():
                                yield chunk
                    except httpx.HTTPError as e:
                        yield f'data: {{"error": {{"message": "stream error: {e}"}}}}\n\n'.encode()
            log_call(session['id'], model, est)
            return StreamingResponse(stream(), media_type="text/event-stream")
        else:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    upstream_url,
                    headers=headers, json=body, timeout=120
                )
                log_call(session['id'], model, est)
                try:
                    return JSONResponse(content=resp.json(), status_code=resp.status_code)
                except Exception:
                    return JSONResponse(
                        content={"error": {"message": resp.text[:500], "type": "upstream_error"}},
                        status_code=resp.status_code if resp.status_code >= 400 else 502
                    )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"上游错误: {e}")


# ==================== Bot 专用 API（限 localhost 或已认证） ====================

_bot_stats = {"started_at": datetime.now().isoformat(), "messages_processed": 0, "codes_generated": 0, "errors": 0}
_bot_config = {"duration_minutes": 300, "reply_template": ""}

class BotCreate(BaseModel):
    duration_minutes: int = Field(default=300, ge=30, le=480)

class BotConfigUpdate(BaseModel):
    duration_minutes: int = None
    reply_template: str = None

def _check_localhost(request: Request):
    """只允许本机调用"""
    host = request.client.host if request.client else ''
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise HTTPException(403, "仅限服务器本地调用")

@app.get("/api/bot/status")
async def bot_status(_local = Depends(_check_localhost)):
    return _bot_stats

@app.get("/api/bot/config")
async def bot_config(_local = Depends(_check_localhost)):
    return _bot_config

@app.put("/api/bot/config")
async def bot_config_update(data: BotConfigUpdate, _local = Depends(_check_localhost)):
    if data.duration_minutes is not None:
        _bot_config["duration_minutes"] = data.duration_minutes
    if data.reply_template is not None:
        _bot_config["reply_template"] = data.reply_template
    return {"ok": True, "config": _bot_config}

@app.post("/api/bot/create-code")
async def bot_create_code(data: BotCreate, request: Request):
    """Bot 专用：生成+激活一步完成，限本机调用"""
    _check_localhost(request)

    try:
        dur = data.duration_minutes
        h, m = dur // 60, dur % 60
        dur_str = f"{h}小时" if m==0 else f"{h}小时{m}分钟" if h>0 else f"{m}分钟"
        result = create_session(dur, price_per_hour=0.6, price_per_request=0.002, notes="Bot自动生成")
        info = activate_session(result['activation_code'])
    except ValueError as e:
        _bot_stats["errors"] += 1
        raise HTTPException(503, f"系统繁忙: {e}")

    _bot_stats["codes_generated"] += 1
    _bot_stats["messages_processed"] += 1

    domain = get_plan().get('public_domain','') or '1.14.105.111'
    return {
        "activation_code": result['activation_code'],
        "duration": dur_str,
        "portal_url": f"http://{domain}/portal",
        "direct_url": f"http://{domain}/portal?code={result['activation_code']}",
        "base_url": f"http://{domain}/v1",
        "api_key": info.get('session_key'),
        "models": get_all_models(),
        "reply_text": (_bot_config["reply_template"]
            .replace("{code}", result['activation_code'])
            .replace("{duration}", dur_str)
            .replace("{portal_url}", f"http://{domain}/portal")
            .replace("{direct_url}", f"http://{domain}/portal?code={result['activation_code']}")
            .replace("{base_url}", f"http://{domain}/v1")
            if _bot_config["reply_template"] else
            f"感谢下单！🎉\n\n激活码：{result['activation_code']}\n自助页：http://{domain}/portal\n\n👉 点击直达：http://{domain}/portal?code={result['activation_code']}\n   (自动激活，拿到 Base URL + API Key 就能用)\n\n⏰ {dur_str}，计时从激活开始\n🤖 可用模型：tc-code-latest / glm-5 / kimi-k2.5 / minimax-m2.5\n🔌 支持 Cursor / CodeBuddy / Claude Code\n\n有问题随时问我～"),
    }


@app.get("/api/health")
async def health():
    return {"status":"ok","time":datetime.now().isoformat()}

def _get_domain(request=None):
    """获取访问域名，优先使用配置的 public_domain"""
    plan = get_plan()
    if plan.get('public_domain'):
        return plan['public_domain']
    if request:
        host = request.headers.get('host','localhost')
        return host  # nginx 代理时会自动透传正确的 Host
    return '1.14.105.111'


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8899)
