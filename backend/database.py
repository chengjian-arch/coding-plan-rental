"""
Coding Plan 激活码租用系统 - 数据库
流程: 管理端生成激活码 → 顾客在自助页输入 → 获取API凭证+开始计时 → 过期作废
"""

import sqlite3, os, uuid, random, string, json
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'rental.db')


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS plan_config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                plan_name TEXT DEFAULT 'Lite',
                monthly_cost REAL DEFAULT 40.0,
                limit_5h INTEGER DEFAULT 1200,
                limit_weekly INTEGER DEFAULT 9000,
                limit_monthly INTEGER DEFAULT 18000,
                cooldown_minutes INTEGER DEFAULT 30,
                min_minutes INTEGER DEFAULT 30,
                max_hours INTEGER DEFAULT 8,
                safety_pct REAL DEFAULT 20.0,
                price_per_hour REAL DEFAULT 0.6,
                price_per_request REAL DEFAULT 0.002,
                coding_plan_api_key TEXT DEFAULT '',
                public_domain TEXT DEFAULT ''
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact TEXT DEFAULT '',
                total_spent REAL DEFAULT 0,
                total_hours REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS rental_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER DEFAULT 0,
                activation_code TEXT UNIQUE NOT NULL,
                duration_minutes INTEGER NOT NULL,
                price_per_hour REAL DEFAULT 0.6,
                price_per_request REAL DEFAULT 0.002,
                base_cost REAL DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                usage_cost REAL DEFAULT 0,
                total_cost REAL DEFAULT 0,
                session_key TEXT,
                activated_at TEXT,
                expires_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS api_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                model TEXT DEFAULT 'auto',
                requests INTEGER DEFAULT 1,
                cost REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage_window (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_type TEXT NOT NULL,
                window_start TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                limit_count INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS api_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                models TEXT DEFAULT '[]',
                is_default INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        ''')
        existing = c.execute('SELECT id FROM plan_config WHERE id=1').fetchone()
        if not existing:
            c.execute('INSERT INTO plan_config (id) VALUES (1)')
        # 迁移: 如果 api_providers 为空且有 coding_plan_api_key, 自动创建默认供应商
        providers = c.execute('SELECT COUNT(*) as cnt FROM api_providers').fetchone()['cnt']
        if providers == 0:
            plan_row = c.execute('SELECT coding_plan_api_key FROM plan_config WHERE id=1').fetchone()
            old_key = plan_row['coding_plan_api_key'] if plan_row else ''
            default_models = json.dumps([
                {"id":"tc-code-latest","name":"Auto (智能匹配)","desc":"自动选择最优模型"},
                {"id":"glm-5","name":"GLM-5","desc":"智谱最新旗舰"},
                {"id":"kimi-k2.5","name":"Kimi K2.5","desc":"长上下文推理"},
                {"id":"minimax-m2.5","name":"MiniMax M2.5","desc":"高效编程模型"},
            ])
            c.execute('''INSERT INTO api_providers (name, base_url, api_key, models, is_default, status)
                         VALUES (?,?,?,?,1,'active')''',
                      ('腾讯云 Coding Plan','https://api.lkeap.cloud.tencent.com/coding/v3',old_key,default_models))
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try: yield conn
    finally: conn.close()


# ==================== 激活码生成 ====================

def _gen_code():
    """生成 6 位易读激活码，如 CP-8A3F"""
    chars = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # 去掉易混淆字符 0/O/1/I/L
    code = ''.join(random.choices(chars, k=4))
    return f"CP{code}"  # 6位: CP + 4位

def _gen_session_key():
    return f"sk-cp-{uuid.uuid4().hex[:24]}"


# ==================== 套餐 ====================

def get_plan():
    with get_conn() as conn:
        r = conn.execute('SELECT * FROM plan_config WHERE id=1').fetchone()
    return dict(r) if r else {}

def update_plan(**kw):
    allowed = ['monthly_cost','cooldown_minutes','min_minutes','max_hours',
               'safety_pct','price_per_hour','price_per_request',
               'coding_plan_api_key','public_domain']
    with get_conn() as conn:
        for k,v in kw.items():
            if k in allowed:
                conn.execute(f'UPDATE plan_config SET {k}=? WHERE id=1',(v,))
        conn.commit()


# ==================== 租户 ====================

def create_tenant(name, contact=''):
    with get_conn() as conn:
        cur = conn.execute('INSERT INTO tenants (name,contact) VALUES (?,?)',(name,contact))
        conn.commit()
        return cur.lastrowid

def get_tenants():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM tenants ORDER BY id').fetchall()]

def get_tenant(tid):
    with get_conn() as conn:
        r = conn.execute('SELECT * FROM tenants WHERE id=?',(tid,)).fetchone()
    return dict(r) if r else None

def update_tenant(tid, **kw):
    allowed = ['name','contact','status']
    with get_conn() as conn:
        for k,v in kw.items():
            if k in allowed:
                conn.execute(f'UPDATE tenants SET {k}=? WHERE id=?',(v,tid))
        conn.commit()


# ==================== API 供应商 ====================

def get_providers():
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM api_providers ORDER BY is_default DESC, id ASC').fetchall()
    return [dict(r) for r in rows]

def get_provider(pid):
    with get_conn() as conn:
        r = conn.execute('SELECT * FROM api_providers WHERE id=?',(pid,)).fetchone()
    return dict(r) if r else None

def create_provider(name, base_url, api_key='', models=None, is_default=False):
    models_json = json.dumps(models) if models else '[]'
    with get_conn() as conn:
        if is_default:
            conn.execute('UPDATE api_providers SET is_default=0')
        cur = conn.execute(
            'INSERT INTO api_providers (name,base_url,api_key,models,is_default,status) VALUES (?,?,?,?,?,\'active\')',
            (name, base_url, api_key, models_json, 1 if is_default else 0)
        )
        conn.commit()
        return cur.lastrowid

def update_provider(pid, **kw):
    allowed = ['name','base_url','api_key','models','is_default','status']
    with get_conn() as conn:
        if kw.get('is_default'):
            conn.execute('UPDATE api_providers SET is_default=0')
        for k,v in kw.items():
            if k in allowed:
                if k == 'models' and isinstance(v, (list, dict)):
                    v = json.dumps(v)
                if k == 'is_default':
                    v = 1 if v else 0
                conn.execute(f'UPDATE api_providers SET {k}=? WHERE id=?',(v,pid))
        conn.commit()

def delete_provider(pid):
    with get_conn() as conn:
        row = conn.execute('SELECT is_default FROM api_providers WHERE id=?',(pid,)).fetchone()
        if row and row['is_default']:
            return False  # 不能删除默认供应商
        conn.execute('DELETE FROM api_providers WHERE id=?',(pid,))
        conn.commit()
    return True

def find_provider_for_model(model_id):
    """根据模型ID找到对应的供应商，没找到则返回默认供应商"""
    with get_conn() as conn:
        providers = [dict(r) for r in conn.execute(
            'SELECT * FROM api_providers WHERE status=\'active\''
        ).fetchall()]
    for p in providers:
        try:
            models = json.loads(p['models']) if isinstance(p['models'], str) else p['models']
            for m in models:
                if m.get('id') == model_id:
                    return p
        except (json.JSONDecodeError, TypeError):
            continue
    # 回退到默认供应商
    for p in providers:
        if p['is_default']:
            return p
    return providers[0] if providers else None

def get_all_models():
    """汇总所有供应商的模型列表"""
    with get_conn() as conn:
        providers = [dict(r) for r in conn.execute(
            'SELECT * FROM api_providers WHERE status=\'active\''
        ).fetchall()]
    models = []
    for p in providers:
        try:
            pmodels = json.loads(p['models']) if isinstance(p['models'], str) else p['models']
            for m in pmodels:
                models.append({
                    'id': m.get('id',''),
                    'name': m.get('name', m.get('id','')),
                    'desc': m.get('desc',''),
                    'provider': p['name'],
                })
        except (json.JSONDecodeError, TypeError):
            continue
    return models


# ==================== 核心：会话 + 激活码 ====================

def create_session(duration_minutes, price_per_hour=0.6, price_per_request=0.002,
                   tenant_id=0, notes=''):
    """
    管理员生成租用码。
    返回 activation_code，管理员发给顾客。
    会话状态 = 'pending'，顾客激活后状态变为 'active'。
    """
    plan = get_plan()
    # 规则检查
    active = _get_active()
    if active:
        raise ValueError(f'当前有活跃会话 (码:{active["activation_code"]}，至{active["expires_at"][:16]})')

    last = _get_last()
    if last:
        # Use ended_at (actual end time) if available, otherwise expires_at
        end_str = last.get('ended_at') or last['expires_at']
        last_end = datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
        cooldown_end = last_end + timedelta(minutes=plan.get('cooldown_minutes', 30))
        if datetime.now() < cooldown_end:
            remain = int((cooldown_end - datetime.now()).total_seconds() / 60)
            raise ValueError(f'冷却中，{cooldown_end.strftime("%H:%M")} 后可生成新码 (还需{remain}分钟)')

    code = _gen_code()
    # 确保唯一
    with get_conn() as conn:
        while conn.execute('SELECT id FROM rental_sessions WHERE activation_code=?',(code,)).fetchone():
            code = _gen_code()

    base_cost = round(duration_minutes / 60 * price_per_hour, 2)

    # 过期时间从激活时算，先设置一个占位值
    # 实际计时从顾客激活开始
    far_future = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    with get_conn() as conn:
        conn.execute('''
            INSERT INTO rental_sessions
            (tenant_id, activation_code, duration_minutes, price_per_hour,
             price_per_request, base_cost, total_cost, expires_at, status, notes)
            VALUES (?,?,?,?,?,?,?,?,'pending',?)
        ''', (tenant_id, code, duration_minutes, price_per_hour,
              price_per_request, base_cost, base_cost, far_future, notes))
        conn.commit()

    return {
        'activation_code': code,
        'duration_minutes': duration_minutes,
        'duration_display': f'{duration_minutes//60}小时{duration_minutes%60}分钟' if duration_minutes >= 60 else f'{duration_minutes}分钟',
        'base_cost': base_cost,
        'price_per_hour': price_per_hour,
        'price_per_request': price_per_request,
    }


def activate_session(activation_code):
    """
    顾客输入激活码，激活会话。
    返回 API 凭证 + 计时信息。
    此后 activation_code 可随时查询状态。
    """
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM rental_sessions WHERE activation_code=?',
            (activation_code,)).fetchone()
        if not row:
            raise ValueError('激活码无效')

        r = dict(row)
        if r['status'] == 'active':
            # 已激活，直接返回当前状态
            return _session_info(r)
        if r['status'] in ('expired', 'cancelled'):
            raise ValueError('该激活码已失效')

        # 检查是否有人在用
        active = _get_active()
        if active and active['id'] != r['id']:
            raise ValueError(f'当前有其他用户在使用中，请稍后再试')

        # 激活：设实际过期时间 + 生成 API Key
        now = datetime.now()
        expires_at = (now + timedelta(minutes=r['duration_minutes'])).strftime('%Y-%m-%d %H:%M:%S')
        session_key = _gen_session_key()

        conn.execute('''
            UPDATE rental_sessions
            SET activated_at=?, expires_at=?, session_key=?, status='active'
            WHERE id=?
        ''', (now.strftime('%Y-%m-%d %H:%M:%S'), expires_at, session_key, r['id']))
        conn.commit()

        r['activated_at'] = now.strftime('%Y-%m-%d %H:%M:%S')
        r['expires_at'] = expires_at
        r['session_key'] = session_key
        r['status'] = 'active'

    return _session_info(r)


def get_session_by_code(activation_code):
    """通过激活码查询会话状态（顾客自助查询）"""
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM rental_sessions WHERE activation_code=?',
            (activation_code,)).fetchone()
        if not row:
            return None
    return _session_info(dict(row))


def end_expired_sessions():
    """自动结束所有已过期的会话"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_conn() as conn:
        expired = conn.execute('''
            SELECT id FROM rental_sessions
            WHERE status='active' AND datetime(expires_at) <= datetime(?)
        ''', (now,)).fetchall()
        for row in expired:
            _end_session(conn, row['id'])
        conn.commit()
    return len(expired)


def end_session_admin(session_id):
    """管理员手动强制结束会话"""
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM rental_sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        if r['status'] in ('expired', 'cancelled'):
            return r
        _end_session(conn, session_id)
        conn.commit()
        updated = conn.execute('SELECT * FROM rental_sessions WHERE id=?', (session_id,)).fetchone()
        return dict(updated) if updated else r


def _end_session(conn, session_id):
    """内部：结束会话，计算费用"""
    row = conn.execute('SELECT * FROM rental_sessions WHERE id=?',(session_id,)).fetchone()
    if not row: return
    r = dict(row)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    usage = conn.execute(
        'SELECT COALESCE(SUM(cost),0) as c FROM api_logs WHERE session_id=?',
        (session_id,)).fetchone()
    usage_cost = round(usage['c'], 4)
    total = round(r['base_cost'] + usage_cost, 2)
    conn.execute('''
        UPDATE rental_sessions
        SET ended_at=?, usage_cost=?, total_cost=?, status='expired', session_key=NULL
        WHERE id=?
    ''', (now, usage_cost, total, session_id))
    if r['tenant_id']:
        conn.execute('''
            UPDATE tenants SET total_spent=total_spent+?, total_hours=total_hours+?
            WHERE id=?
        ''', (total, r['duration_minutes']/60.0, r['tenant_id']))


# ==================== API 代理鉴权 ====================

def validate_session_key(session_key):
    """验证 API Key 是否有效，返回 session 信息"""
    end_expired_sessions()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_conn() as conn:
        row = conn.execute('''
            SELECT * FROM rental_sessions
            WHERE session_key=? AND status='active'
              AND datetime(expires_at) > datetime(?)
        ''', (session_key, now)).fetchone()
    return dict(row) if row else None


# ==================== 用量追踪 ====================

def log_call(session_id, model='auto', requests=1):
    plan = get_plan()
    cost_per = plan.get('price_per_request', 0.002) * requests
    cost = round(cost_per, 4)
    with get_conn() as conn:
        conn.execute('INSERT INTO api_logs (session_id,model,requests,cost) VALUES (?,?,?,?)',
                     (session_id, model, requests, cost))
        conn.execute('UPDATE rental_sessions SET request_count=request_count+? WHERE id=?',
                     (requests, session_id))
        _update_window(conn, '5h', requests, plan['limit_5h'])
        _update_window(conn, 'weekly', requests, plan['limit_weekly'])
        _update_window(conn, 'monthly', requests, plan['limit_monthly'])
        conn.commit()


def _update_window(conn, wtype, count, limit):
    now = datetime.now()
    if wtype == '5h': start = now - timedelta(hours=5)
    elif wtype == 'weekly': start = now - timedelta(weeks=1)
    else: start = now.replace(day=1, hour=0, minute=0, second=0)
    start_s = start.strftime('%Y-%m-%d %H:%M:%S')
    row = conn.execute(
        'SELECT id FROM usage_window WHERE window_type=? AND window_start=?',
        (wtype, start_s)).fetchone()
    if row:
        conn.execute('UPDATE usage_window SET used=used+? WHERE id=?',(count,row['id']))
    else:
        conn.execute('INSERT INTO usage_window (window_type,window_start,used,limit_count) VALUES (?,?,?,?)',
                     (wtype, start_s, count, limit))


def get_usage():
    plan = get_plan()
    result = {}
    for wt in ['5h','weekly','monthly']:
        now = datetime.now()
        if wt == '5h': start = now - timedelta(hours=5)
        elif wt == 'weekly': start = now - timedelta(weeks=1)
        else: start = now.replace(day=1, hour=0, minute=0, second=0)
        start_s = start.strftime('%Y-%m-%d %H:%M:%S')
        with get_conn() as conn:
            row = conn.execute(
                'SELECT used FROM usage_window WHERE window_type=? AND window_start=?',
                (wt, start_s)).fetchone()
        used = row['used'] if row else 0
        limit = plan[f'limit_{"5h" if wt=="5h" else wt}']
        pct = round(used/limit*100,1) if limit else 0
        result[wt] = {'used':used,'limit':limit,'pct':pct,
                       'level':'danger' if pct>=90 else ('warning' if pct>=70 else 'safe')}
    return result


# ==================== 查询/列表 ====================

def get_sessions(limit=100):
    end_expired_sessions()
    with get_conn() as conn:
        rows = conn.execute('''
            SELECT s.*, t.name as tenant_name
            FROM rental_sessions s LEFT JOIN tenants t ON s.tenant_id=t.id
            ORDER BY s.id DESC LIMIT ?
        ''',(limit,)).fetchall()
    return [dict(r) for r in rows]

def get_logs(session_id=None, limit=50):
    with get_conn() as conn:
        if session_id:
            rows = conn.execute(
                'SELECT * FROM api_logs WHERE session_id=? ORDER BY id DESC LIMIT ?',
                (session_id,limit)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM api_logs ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
    return [dict(r) for r in rows]

def get_stats():
    end_expired_sessions()
    plan = get_plan()
    today = datetime.now().strftime('%Y-%m-%d')
    with get_conn() as conn:
        today_count = conn.execute(
            "SELECT COUNT(*) as c FROM rental_sessions WHERE date(created_at)=?",(today,)).fetchone()['c']
        today_revenue = conn.execute(
            "SELECT COALESCE(SUM(total_cost),0) as c FROM rental_sessions WHERE date(created_at)=? AND status IN ('expired','active')",
            (today,)).fetchone()['c']
        month_revenue = conn.execute(
            "SELECT COALESCE(SUM(total_cost),0) as c FROM rental_sessions WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now') AND status IN ('expired','active')"
        ).fetchone()['c']
    active = _get_active()
    return {
        'plan': plan,
        'today_count': today_count,
        'today_revenue': round(today_revenue, 2),
        'month_revenue': round(month_revenue, 2),
        'profit': round(month_revenue - plan['monthly_cost'], 2),
        'active': active is not None,
        'active_session': active,
        'usage': get_usage(),
    }


# ==================== 内部 ====================

def _get_active():
    end_expired_sessions()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with get_conn() as conn:
        row = conn.execute('''
            SELECT s.*, t.name as tenant_name
            FROM rental_sessions s LEFT JOIN tenants t ON s.tenant_id=t.id
            WHERE s.status='active' AND datetime(s.expires_at) > datetime(?)
            ORDER BY s.id DESC LIMIT 1
        ''',(now,)).fetchone()
    return dict(row) if row else None

def _get_last():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM rental_sessions WHERE status IN ('expired','cancelled') ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None

def _session_info(r):
    """格式化会话信息返回给前端"""
    now = datetime.now()
    expires = datetime.strptime(r['expires_at'], '%Y-%m-%d %H:%M:%S')
    remaining_sec = max(0, int((expires - now).total_seconds()))
    remaining_min = remaining_sec // 60
    remaining_sec_part = remaining_sec % 60

    activated = r.get('activated_at')
    if activated:
        activated_dt = datetime.strptime(activated, '%Y-%m-%d %H:%M:%S')
        elapsed_sec = int((now - activated_dt).total_seconds())
        elapsed_min = max(0, elapsed_sec // 60)
    else:
        elapsed_min = 0

    total_min = r['duration_minutes']

    return {
        'id': r['id'],
        'activation_code': r['activation_code'],
        'status': r['status'],
        'duration_minutes': total_min,
        'elapsed_minutes': elapsed_min,
        'remaining_seconds': remaining_sec,
        'remaining_display': f'{remaining_min}分{remaining_sec_part}秒' if remaining_sec > 0 else '已过期',
        'expires_at': r['expires_at'],
        'activated_at': activated,
        'session_key': r.get('session_key'),
        'request_count': r['request_count'],
        'base_cost': r['base_cost'],
        'usage_cost': r.get('usage_cost', 0),
        'total_cost': r['total_cost'],
        'price_per_hour': r['price_per_hour'],
        'price_per_request': r['price_per_request'],
    }
