"""
GitHub & Devin 自动化工具箱 — Web GUI 版（整合版）
=====================================================
在浏览器中打开 http://localhost:5000 即可使用
功能：登录验证 / 2FA 开启 / PAT 创建 / 仓库管理 / Devin 注册登录
本地 SQLite 持久化 + JSON 导出

依赖：pip install requests flask pyotp
      pip install playwright && playwright install chromium  (PAT/2FA/Devin 功能需要)
"""

import base64
import json
import os
import re
import sqlite3
import time
from datetime import datetime

from flask import Flask, render_template_string, request, jsonify, Response
import requests as req_lib

try:
    import pyotp
except ImportError:
    pyotp = None

app = Flask(__name__)

# ─── 数据库 ──────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            email     TEXT NOT NULL,
            password  TEXT NOT NULL,
            totp      TEXT DEFAULT '',
            username  TEXT DEFAULT '',
            recovery  TEXT DEFAULT '',
            created   TEXT DEFAULT (datetime('now','localtime')),
            updated   TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS pats (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            token     TEXT NOT NULL,
            name      TEXT DEFAULT '',
            perms     TEXT DEFAULT '',
            expires   TEXT DEFAULT '',
            created   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );
        CREATE TABLE IF NOT EXISTS repos (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            name       TEXT NOT NULL,
            full_name  TEXT DEFAULT '',
            url        TEXT DEFAULT '',
            visibility TEXT DEFAULT 'public',
            created    TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );
        CREATE TABLE IF NOT EXISTS devin (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            action     TEXT DEFAULT 'signup',
            url        TEXT DEFAULT '',
            status     TEXT DEFAULT '',
            created    TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        );
        """)


_init_db()


def _save_account(email, password, totp="", username="", recovery=""):
    with _db() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE accounts SET password=?,totp=?,username=?,recovery=?,updated=datetime('now','localtime') WHERE id=?",
                (password, totp, username, recovery, row["id"]),
            )
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO accounts(email,password,totp,username,recovery) VALUES(?,?,?,?,?)",
                (email, password, totp, username, recovery),
            )
            return cur.lastrowid


def _save_pat(account_id, token, name="", perms="", expires=""):
    with _db() as conn:
        conn.execute(
            "INSERT INTO pats(account_id,token,name,perms,expires) VALUES(?,?,?,?,?)",
            (account_id, token, name, perms, expires),
        )


def _save_repo(account_id, name, full_name="", url="", visibility="public"):
    with _db() as conn:
        conn.execute(
            "INSERT INTO repos(account_id,name,full_name,url,visibility) VALUES(?,?,?,?,?)",
            (account_id, name, full_name, url, visibility),
        )


def _save_devin(account_id, action, url="", status=""):
    with _db() as conn:
        conn.execute(
            "INSERT INTO devin(account_id,action,url,status) VALUES(?,?,?,?)",
            (account_id, action, url, status),
        )


def _get_account_id(email):
    with _db() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
        return row["id"] if row else None


# ─── HTTP 工具 ────────────────────────────────────────────────

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
API_BASE = "https://api.github.com"


def _extract_csrf(html, field_name="authenticity_token"):
    m = re.search(rf'name="{field_name}"\s+value="([^"]+)"', html)
    if not m:
        raise ValueError(f"无法提取 {field_name}")
    return m.group(1)


def _totp_code_online(secret, api_url="https://kloping.top/api/2fa0"):
    resp = req_lib.get(f"{api_url}?secret={secret}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", "")
    if not code:
        raise ValueError(f"获取 TOTP 验证码失败: {data}")
    return code


def _totp_code_local(secret):
    if not pyotp:
        raise RuntimeError("pyotp 未安装，请 pip install pyotp")
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).now()


def _totp_code(secret):
    try:
        return _totp_code_local(secret)
    except Exception:
        return _totp_code_online(secret)


def _new_session():
    s = req_lib.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    return s


def _api_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }


# ─── 核心功能 ─────────────────────────────────────────────────

def do_login(username, password, totp_secret):
    """HTTP 登录 GitHub，返回 (success, username, totp_code, logs)"""
    logs = []
    totp_code = ""
    try:
        session = _new_session()
        logs.append("[1/4] 获取登录页面...")
        resp = session.get("https://github.com/login", timeout=15)
        resp.raise_for_status()
        token = _extract_csrf(resp.text)
        logs.append(f"      CSRF token: {token[:20]}...")

        logs.append("[2/4] 提交登录凭据...")
        resp = session.post(
            "https://github.com/session",
            data={
                "commit": "Sign in",
                "authenticity_token": token,
                "login": username,
                "password": password,
                "webauthn-conditional": "undefined",
                "javascript-support": "true",
                "webauthn-support": "supported",
                "webauthn-iuvpaa-support": "unsupported",
                "return_to": "", "timestamp": "", "timestamp_secret": "",
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        if "/sessions/two-factor" in resp.url:
            logs.append("[3/4] 需要两步验证，获取 TOTP 验证码...")
            totp_code = _totp_code(totp_secret)
            logs.append(f"      TOTP 验证码: {totp_code}")
            token_2fa = _extract_csrf(resp.text)
            resp = session.post(
                "https://github.com/sessions/two-factor",
                data={"authenticity_token": token_2fa, "app_otp": totp_code},
                timeout=15, allow_redirects=True,
            )
            resp.raise_for_status()
        else:
            logs.append("[3/4] 无需两步验证，跳过")

        logs.append("[4/4] 验证登录状态...")
        if "github.com/login" in resp.url:
            raise RuntimeError("被重定向回登录页面")

        check = session.get("https://github.com/settings/profile", timeout=15)
        if check.status_code == 200 and "Public profile" in check.text:
            m = re.search(r'"login":"([^"]+)"', check.text)
            logged_user = m.group(1) if m else "未知"
            logs.append(f"      登录成功! 当前用户: {logged_user}")
            return True, logged_user, totp_code, logs
        else:
            raise RuntimeError(f"验证失败: status={check.status_code}")
    except Exception as e:
        logs.append(f"错误: {e}")
        return False, "", totp_code, logs


# ─── HTML 模板 ────────────────────────────────────────────────

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub & Devin 自动化工具箱</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh;padding:24px 20px}
.wrap{max-width:860px;margin:0 auto}
.header{text-align:center;margin-bottom:20px}
.header h1{font-size:22px;color:#f0f6fc;margin-bottom:4px}
.header p{font-size:12px;color:#8b949e}
/* Tabs */
.tabs{display:flex;gap:0;border-bottom:1px solid #30363d;margin-bottom:16px}
.tab{padding:10px 18px;font-size:13px;color:#8b949e;cursor:pointer;border-bottom:2px solid transparent;transition:.2s}
.tab:hover{color:#c9d1d9}
.tab.active{color:#f0f6fc;border-bottom-color:#58a6ff}
.tab-body{display:none}
.tab-body.active{display:block}
/* Card */
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:16px;margin-bottom:14px}
.card h2{font-size:13px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #21262d}
/* Form */
.fg{margin-bottom:10px}
.fg label{display:block;font-size:12px;color:#c9d1d9;margin-bottom:4px;font-weight:500}
.fg input,.fg textarea,.fg select{width:100%;padding:7px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;transition:.2s}
.fg input:focus,.fg textarea:focus,.fg select:focus{border-color:#58a6ff}
.fg input::placeholder,.fg textarea::placeholder{color:#484f58}
.fg textarea{font-family:Consolas,Monaco,monospace;resize:vertical}
.fg select{appearance:none;-webkit-appearance:none;cursor:pointer}
.row{display:flex;gap:10px;align-items:flex-end}
.row .fg{flex:1}
/* Buttons */
.btn-row{display:flex;gap:8px;margin-top:12px}
.btn{padding:8px 16px;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;transition:.2s;flex:1}
.btn-g{background:#238636;color:#fff}.btn-g:hover{background:#2ea043}
.btn-g:disabled{background:#21262d;color:#484f58;cursor:not-allowed}
.btn-s{background:#21262d;color:#c9d1d9;border:1px solid #30363d}.btn-s:hover{background:#30363d}
.btn-d{background:#da3633;color:#fff}.btn-d:hover{background:#b62324}
.btn-sm{padding:5px 12px;font-size:12px;flex:none}
/* Status */
.sbar{padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px;display:none}
.s-ok{display:block;background:#0d2818;border:1px solid #238636;color:#3fb950}
.s-err{display:block;background:#2d1117;border:1px solid #da3633;color:#f85149}
.s-load{display:block;background:#1a1e24;border:1px solid #30363d;color:#8b949e}
/* Log */
.log{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;font-family:Consolas,Monaco,monospace;font-size:11px;line-height:1.6;max-height:220px;overflow-y:auto;white-space:pre-wrap;color:#8b949e}
.log .ok{color:#3fb950}.log .err{color:#f85149}.log .info{color:#58a6ff}
/* Spinner */
.sp{display:inline-block;width:12px;height:12px;border:2px solid #484f58;border-top-color:#58a6ff;border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:5px}
@keyframes spin{to{transform:rotate(360deg)}}
/* Result table */
.rtbl{width:100%;font-size:12px;border-collapse:collapse;margin-top:8px}
.rtbl th{text-align:left;color:#8b949e;padding:4px 6px;border-bottom:1px solid #21262d;font-weight:500}
.rtbl td{padding:4px 6px;border-bottom:1px solid #21262d}
.rtbl tr:hover{background:#1c2128}
.copy-btn{background:none;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer}
.copy-btn:hover{color:#f0f6fc;border-color:#58a6ff}
/* History card btn */
.hdr-row{display:flex;justify-content:space-between;align-items:center}
/* Account Selector */
.acct-bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;align-items:center}
.acct-bar .ab-label{font-size:11px;color:#8b949e;white-space:nowrap}
.acct-chip{display:flex;align-items:center;gap:6px;padding:6px 12px;background:#161b22;border:1px solid #30363d;border-radius:20px;cursor:pointer;transition:.2s;font-size:12px;color:#c9d1d9;user-select:none}
.acct-chip:hover{border-color:#58a6ff;background:#1c2128}
.acct-chip.active{border-color:#238636;background:#0d2818;color:#3fb950}
.acct-chip .ac-avatar{width:20px;height:20px;border-radius:50%;background:#21262d;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#8b949e}
.acct-chip.active .ac-avatar{background:#238636;color:#fff}
.acct-chip .ac-name{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.acct-chip .ac-user{font-size:10px;color:#8b949e}
.acct-chip.active .ac-user{color:#3fb950}
.acct-chip .ac-remove{font-size:10px;color:#484f58;margin-left:2px;padding:2px 4px;border-radius:3px}
.acct-chip .ac-remove:hover{color:#f85149;background:#2d1117}
.acct-empty{font-size:12px;color:#484f58;padding:8px 0}
</style>
</head>
<body>
<div class="wrap">
<div class="header">
 <h1>GitHub & Devin 自动化工具箱</h1>
 <p>登录验证 | 2FA 管理 | PAT 创建 | 仓库管理 | Devin 注册/登录 | 本地持久化</p>
</div>

<div id="sbar" class="sbar"></div>

<!-- 账号选择器 -->
<div class="card" id="acctCard">
 <h2>账号切换</h2>
 <div class="acct-bar" id="acctBar">
  <span class="acct-empty" id="acctEmpty">暂无已保存的账号，完成登录或 2FA 后自动保存</span>
 </div>
</div>

<!-- 输入区 -->
<div class="card">
 <h2>账号输入</h2>
 <div class="fg">
  <label>输入账号信息（每行一个，用 ---- 分隔）</label>
  <textarea id="inp" rows="3" placeholder="支持两种格式:&#10;账号----密码          → 自动开启 2FA 获取 TOTP&#10;账号----密码----TOTP  → 直接登录验证"></textarea>
 </div>
 <div class="btn-row">
  <button class="btn btn-g" id="goBtn" onclick="go()">开始</button>
  <button class="btn btn-s" onclick="clr()">清空</button>
 </div>
</div>

<!-- Tabs -->
<div class="tabs">
 <div class="tab active" onclick="sw(this,0)">登录 & 2FA</div>
 <div class="tab" onclick="sw(this,1)">PAT 创建</div>
 <div class="tab" onclick="sw(this,2)">仓库管理</div>
 <div class="tab" onclick="sw(this,3)">Devin</div>
 <div class="tab" onclick="sw(this,4)">历史记录</div>
</div>

<!-- Tab 0: 登录 & 2FA -->
<div class="tab-body active" id="t0">
 <div class="card"><h2>运行日志</h2><div class="log" id="log0">等待操作...</div></div>
 <div id="res0"></div>
</div>

<!-- Tab 1: PAT -->
<div class="tab-body" id="t1">
 <div class="card">
  <h2>PAT 创建参数</h2>
  <div class="row">
   <div class="fg"><label>Token 名称（留空自动生成）</label><input id="patName" placeholder="Auto-PAT-20260605"></div>
   <div class="fg"><label>过期天数</label><input id="patDays" type="number" value="365" min="1" max="366"></div>
  </div>
  <div class="fg"><label>仓库范围</label>
   <select id="patRepo"><option value="all">所有仓库</option><option value="public">仅公开仓库</option></select>
  </div>
  <div class="btn-row"><button class="btn btn-g" id="patBtn" onclick="createPat()">创建 PAT</button></div>
 </div>
 <div class="card"><h2>运行日志</h2><div class="log" id="log1">等待操作...</div></div>
 <div id="res1"></div>
</div>

<!-- Tab 2: 仓库管理 -->
<div class="tab-body" id="t2">
 <div class="card">
  <h2>仓库操作</h2>
  <div class="fg"><label>PAT Token</label><input id="repoToken" placeholder="github_pat_xxxx...（创建 PAT 后自动填入）"></div>
  <div class="row">
   <div class="fg"><label>名称模式</label><input id="repoName" value="repo-{rand}" placeholder="repo-{rand} 或 proj-{i}"></div>
   <div class="fg" style="flex:0 0 80px"><label>数量</label><input id="repoCount" type="number" value="1" min="1" max="100"></div>
   <div class="fg" style="flex:0 0 80px"><label>可见性</label>
    <select id="repoVis"><option value="public">公开</option><option value="private">私有</option></select>
   </div>
  </div>
  <div class="fg"><label>描述</label><input id="repoDesc" placeholder="可选"></div>
  <div class="fg"><label>初始 README 内容</label><input id="repoInit" placeholder="可选"></div>
  <div class="btn-row">
   <button class="btn btn-g" onclick="repoAction('create')">创建仓库</button>
   <button class="btn btn-s" onclick="repoAction('list')">列出仓库</button>
   <button class="btn btn-d btn-sm" onclick="repoAction('delete')">删除仓库</button>
  </div>
 </div>
 <div class="card"><h2>运行日志</h2><div class="log" id="log2">等待操作...</div></div>
 <div id="res2"></div>
</div>

<!-- Tab 3: Devin -->
<div class="tab-body" id="t3">
 <div class="card">
  <h2>Devin 注册 / 登录</h2>
  <p style="font-size:12px;color:#8b949e;margin-bottom:12px">通过 GitHub OAuth 自动注册或登录 Devin（需要 Playwright）</p>
  <div class="btn-row">
   <button class="btn btn-g" id="dvSignup" onclick="devinAction('signup')">注册 Devin</button>
   <button class="btn btn-s" id="dvLogin" onclick="devinAction('signin')">登录 Devin</button>
  </div>
 </div>
 <div class="card"><h2>运行日志</h2><div class="log" id="log3">等待操作...</div></div>
 <div id="res3"></div>
</div>

<!-- Tab 4: 历史记录 -->
<div class="tab-body" id="t4">
 <div class="card">
  <div class="hdr-row">
   <h2 style="border:none;margin:0;padding:0">历史记录</h2>
   <div>
    <button class="btn btn-s btn-sm" onclick="loadHistory()">刷新</button>
    <button class="btn btn-s btn-sm" onclick="exportJson('accounts')">导出账号 JSON</button>
    <button class="btn btn-s btn-sm" onclick="exportJson('pats')">导出 PAT JSON</button>
    <button class="btn btn-s btn-sm" onclick="exportJson('repos')">导出仓库 JSON</button>
    <button class="btn btn-s btn-sm" onclick="exportJson('all')">导出全部</button>
   </div>
  </div>
 </div>
 <div id="histContent"></div>
</div>

</div><!-- /wrap -->

<script>
// ─── Tab 切换 ───
function sw(el,i){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-body').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('t'+i).classList.add('active');
  if(i===4) loadHistory();
}

// ─── 通用 ───
function ss(type,msg){const b=document.getElementById('sbar');b.className='sbar s-'+type;b.innerHTML=(type==='load'?'<span class="sp"></span>':'')+msg}
function al(id,msg,type){const b=document.getElementById(id);const c=type?type:'';b.innerHTML+='<span class="'+c+'">'+msg+'</span>\n';b.scrollTop=b.scrollHeight}
function clr(){document.getElementById('inp').value='';document.getElementById('sbar').style.display='none';['log0','log1','log2','log3'].forEach(id=>{document.getElementById(id).innerHTML='等待操作...'});['res0','res1','res2','res3'].forEach(id=>{document.getElementById(id).innerHTML=''})}

function copyText(text){navigator.clipboard.writeText(text).then(()=>{ss('ok','已复制到剪贴板')}).catch(()=>{prompt('复制:',text)})}

function parseInput(){
  const raw=document.getElementById('inp').value.trim();
  if(!raw){ss('err','请输入账号信息');return null}
  const lines=raw.split('\n').map(l=>l.trim()).filter(l=>l);
  const accounts=[];
  for(let i=0;i<lines.length;i++){
    const parts=lines[i].split('----').map(p=>p.trim());
    if(parts.length===2){
      accounts.push({email:parts[0],password:parts[1],totp:'',mode:'2fa'});
    }else if(parts.length===3){
      accounts.push({email:parts[0],password:parts[1],totp:parts[2],mode:'login'});
    }else{
      ss('err','第'+(i+1)+'行格式错误，需要 2 段或 3 段（用 ---- 分隔）');return null;
    }
  }
  return accounts;
}

// ─── Tab 0: 登录 & 2FA ───
async function go(){
  const accounts=parseInput();if(!accounts)return;
  const btn=document.getElementById('goBtn');btn.disabled=true;
  document.getElementById('log0').innerHTML='';document.getElementById('res0').innerHTML='';
  let html='<div class="card"><table class="rtbl"><tr><th>账号</th><th>模式</th><th>状态</th><th>结果</th><th>操作</th></tr>';
  for(let i=0;i<accounts.length;i++){
    const a=accounts[i];
    const modeLabel=a.mode==='2fa'?'2FA 开启':'登录验证';
    ss('load','('+((i+1))+'/'+accounts.length+') '+modeLabel+': '+a.email);
    if(accounts.length>1)al('log0','\n━━━ '+(i+1)+'/'+accounts.length+': '+a.email+' ('+modeLabel+') ━━━','info');
    try{
      const resp=await fetch('/api/go',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(a)});
      const d=await resp.json();
      (d.logs||[]).forEach(l=>{let t='';if(l.includes('成功')||l.includes('TOTP')||l.includes('Secret'))t='ok';else if(l.includes('错误')||l.includes('失败'))t='err';else if(l.startsWith('['))t='info';al('log0',l,t)});
      if(d.success){
        const cred=d.credential||'';
        html+='<tr><td>'+a.email+'</td><td>'+modeLabel+'</td><td style="color:#3fb950">成功'+(d.username?' ('+d.username+')':'')+'</td><td style="font-family:monospace;font-size:11px">'+cred.substring(0,40)+(cred.length>40?'...':'')+'</td><td><button class="copy-btn" onclick="copyText(\''+cred.replace(/'/g,"\\'")+'\')">复制</button></td></tr>';
      }else{
        html+='<tr><td>'+a.email+'</td><td>'+modeLabel+'</td><td style="color:#f85149">失败: '+d.error+'</td><td>-</td><td>-</td></tr>';
      }
    }catch(e){
      al('log0','网络错误: '+e.message,'err');
      html+='<tr><td>'+a.email+'</td><td>'+modeLabel+'</td><td style="color:#f85149">网络错误</td><td>-</td><td>-</td></tr>';
    }
  }
  html+='</table></div>';
  document.getElementById('res0').innerHTML=html;
  ss('ok','全部完成');btn.disabled=false;
  loadAccounts();
}

// ─── Tab 1: PAT ───
async function createPat(){
  const accounts=parseInput();if(!accounts)return;
  const a=accounts[0];if(!a.totp){ss('err','创建 PAT 需要 3 段格式（含 TOTP）');return}
  const btn=document.getElementById('patBtn');btn.disabled=true;
  document.getElementById('log1').innerHTML='';document.getElementById('res1').innerHTML='';
  ss('load','创建 PAT 中（浏览器自动化，约 30 秒）...');
  try{
    const resp=await fetch('/api/pat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      email:a.email,password:a.password,totp:a.totp,
      name:document.getElementById('patName').value.trim(),
      days:parseInt(document.getElementById('patDays').value)||365,
      repo_access:document.getElementById('patRepo').value
    })});
    const d=await resp.json();
    (d.logs||[]).forEach(l=>{let t='';if(l.includes('Token')||l.includes('成功'))t='ok';else if(l.includes('错误')||l.includes('失败'))t='err';else if(l.startsWith('['))t='info';al('log1',l,t)});
    if(d.success){
      ss('ok','PAT 创建成功');
      document.getElementById('repoToken').value=d.token;
      document.getElementById('res1').innerHTML='<div class="card" style="border-color:#238636"><h2 style="color:#3fb950">PAT 已生成</h2><div class="fg"><input value="'+d.token+'" readonly style="color:#3fb950;font-family:monospace"></div><button class="copy-btn" onclick="copyText(\''+d.token+'\')">复制 Token</button></div>';
      loadAccounts();
    }else{ss('err','PAT 创建失败: '+d.error)}
  }catch(e){ss('err','网络错误: '+e.message)}
  btn.disabled=false;
}

// ─── Tab 2: 仓库 ───
async function repoAction(action){
  const token=document.getElementById('repoToken').value.trim();
  if(!token){ss('err','请先输入或创建 PAT Token');return}
  document.getElementById('log2').innerHTML='';document.getElementById('res2').innerHTML='';
  if(action==='delete'){
    const name=prompt('输入要删除的仓库名（格式: owner/repo）');if(!name)return;
    ss('load','删除仓库 '+name+'...');
    const resp=await fetch('/api/repo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete',token:token,name:name})});
    const d=await resp.json();(d.logs||[]).forEach(l=>al('log2',l,l.includes('成功')?'ok':l.includes('失败')?'err':''));
    ss(d.success?'ok':'err',d.success?'删除成功':'删除失败');return;
  }
  if(action==='list'){
    ss('load','获取仓库列表...');
    const resp=await fetch('/api/repo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'list',token:token})});
    const d=await resp.json();(d.logs||[]).forEach(l=>al('log2',l,''));
    if(d.success&&d.repos){
      let h='<div class="card"><table class="rtbl"><tr><th>仓库</th><th>可见性</th><th>创建时间</th><th>URL</th></tr>';
      d.repos.forEach(r=>{h+='<tr><td>'+r.full_name+'</td><td>'+r.visibility+'</td><td>'+r.created+'</td><td><a href="'+r.url+'" target="_blank" style="color:#58a6ff">打开</a></td></tr>'});
      h+='</table></div>';document.getElementById('res2').innerHTML=h;
    }
    ss(d.success?'ok':'err',d.success?'共 '+(d.repos||[]).length+' 个仓库':'获取失败');return;
  }
  // create
  ss('load','创建仓库中...');
  const resp=await fetch('/api/repo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    action:'create',token:token,
    pattern:document.getElementById('repoName').value.trim()||'repo-{rand}',
    count:parseInt(document.getElementById('repoCount').value)||1,
    visibility:document.getElementById('repoVis').value,
    description:document.getElementById('repoDesc').value.trim(),
    init_content:document.getElementById('repoInit').value.trim()
  })});
  const d=await resp.json();(d.logs||[]).forEach(l=>al('log2',l,l.includes('成功')||l.includes('✓')?'ok':l.includes('失败')?'err':'info'));
  ss(d.success?'ok':'err',d.message||'完成');
}

// ─── Tab 3: Devin ───
async function devinAction(mode){
  const accounts=parseInput();if(!accounts)return;
  const a=accounts[0];if(!a.totp){ss('err','Devin 需要 3 段格式（含 TOTP）');return}
  const btn=document.getElementById(mode==='signup'?'dvSignup':'dvLogin');btn.disabled=true;
  document.getElementById('log3').innerHTML='';document.getElementById('res3').innerHTML='';
  const label=mode==='signup'?'注册':'登录';
  ss('load','Devin '+label+'中（浏览器自动化，约 35 秒）...');
  try{
    const resp=await fetch('/api/devin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:a.email,password:a.password,totp:a.totp,mode:mode})});
    const d=await resp.json();
    (d.logs||[]).forEach(l=>{let t='';if(l.includes('成功')||l.includes('完成'))t='ok';else if(l.includes('错误')||l.includes('失败'))t='err';else if(l.startsWith('['))t='info';al('log3',l,t)});
    if(d.success){
      ss('ok','Devin '+label+'成功');
      document.getElementById('res3').innerHTML='<div class="card" style="border-color:#238636"><p style="color:#3fb950">'+label+'成功!</p><p style="margin-top:8px;font-size:12px;color:#8b949e">URL: <a href="'+d.url+'" target="_blank" style="color:#58a6ff">'+d.url+'</a></p></div>';
      loadAccounts();
    }else{ss('err','Devin '+label+'失败: '+d.error)}
  }catch(e){ss('err','网络错误: '+e.message)}
  btn.disabled=false;
}

// ─── Tab 4: 历史记录 ───
async function loadHistory(){
  const resp=await fetch('/api/history');const d=await resp.json();
  let h='';
  // Accounts
  h+='<div class="card"><h2>账号 ('+d.accounts.length+')</h2>';
  if(d.accounts.length){
    h+='<table class="rtbl"><tr><th>邮箱</th><th>用户名</th><th>TOTP</th><th>更新时间</th><th>操作</th></tr>';
    d.accounts.forEach(a=>{
      const cred=a.email+'----'+a.password+(a.totp?'----'+a.totp:'');
      h+='<tr><td>'+a.email+'</td><td>'+(a.username||'-')+'</td><td>'+(a.totp?a.totp.substring(0,8)+'...':'无')+'</td><td>'+a.updated+'</td><td><button class="copy-btn" onclick="copyText(\''+cred.replace(/'/g,"\\'")+'\')">复制凭据</button> <button class="copy-btn" onclick="fillAccount(\''+a.email.replace(/'/g,"\\'")+'\',\''+a.password.replace(/'/g,"\\'")+'\',\''+(a.totp||'').replace(/'/g,"\\'")+'\')">回填</button> <button class="copy-btn" style="color:#f85149" onclick="delRecord(\'accounts\','+a.id+')">删除</button></td></tr>';
    });
    h+='</table>';
  }else h+='<p style="color:#484f58;font-size:12px">暂无记录</p>';
  h+='</div>';
  // PATs
  h+='<div class="card"><h2>PAT ('+d.pats.length+')</h2>';
  if(d.pats.length){
    h+='<table class="rtbl"><tr><th>名称</th><th>Token</th><th>过期</th><th>创建时间</th><th>操作</th></tr>';
    d.pats.forEach(p=>{
      h+='<tr><td>'+(p.name||'-')+'</td><td style="font-family:monospace;font-size:11px">'+p.token.substring(0,20)+'...</td><td>'+(p.expires||'-')+'</td><td>'+p.created+'</td><td><button class="copy-btn" onclick="copyText(\''+p.token+'\')">复制</button> <button class="copy-btn" onclick="document.getElementById(\'repoToken\').value=\''+p.token+'\';sw(document.querySelectorAll(\'.tab\')[2],2)">用于仓库</button> <button class="copy-btn" style="color:#f85149" onclick="delRecord(\'pats\','+p.id+')">删除</button></td></tr>';
    });
    h+='</table>';
  }else h+='<p style="color:#484f58;font-size:12px">暂无记录</p>';
  h+='</div>';
  // Repos
  h+='<div class="card"><h2>仓库 ('+d.repos.length+')</h2>';
  if(d.repos.length){
    h+='<table class="rtbl"><tr><th>仓库</th><th>可见性</th><th>创建时间</th><th>操作</th></tr>';
    d.repos.forEach(r=>{
      h+='<tr><td>'+r.full_name+'</td><td>'+r.visibility+'</td><td>'+r.created+'</td><td><a href="'+r.url+'" target="_blank" style="color:#58a6ff">打开</a> <button class="copy-btn" style="color:#f85149" onclick="delRecord(\'repos\','+r.id+')">删除记录</button></td></tr>';
    });
    h+='</table>';
  }else h+='<p style="color:#484f58;font-size:12px">暂无记录</p>';
  h+='</div>';
  // Devin
  h+='<div class="card"><h2>Devin ('+d.devin.length+')</h2>';
  if(d.devin.length){
    h+='<table class="rtbl"><tr><th>操作</th><th>URL</th><th>状态</th><th>时间</th><th></th></tr>';
    d.devin.forEach(v=>{
      h+='<tr><td>'+v.action+'</td><td><a href="'+v.url+'" target="_blank" style="color:#58a6ff">'+v.url.substring(0,40)+'</a></td><td>'+v.status+'</td><td>'+v.created+'</td><td><button class="copy-btn" style="color:#f85149" onclick="delRecord(\'devin\','+v.id+')">删除</button></td></tr>';
    });
    h+='</table>';
  }else h+='<p style="color:#484f58;font-size:12px">暂无记录</p>';
  h+='</div>';
  document.getElementById('histContent').innerHTML=h;
}

function fillAccount(e,p,t){document.getElementById('inp').value=e+'----'+p+(t?'----'+t:'');ss('ok','已回填到输入框')}

// ─── 账号选择器 ───
let currentAcctId=null;
let _acctCache=[];
async function loadAccounts(){
  try{
    const resp=await fetch('/api/accounts');
    const d=await resp.json();
    _acctCache=d.accounts||[];
    const bar=document.getElementById('acctBar');
    if(_acctCache.length===0){bar.innerHTML='<span class=\"acct-empty\">\u6682\u65e0\u5df2\u4fdd\u5b58\u7684\u8d26\u53f7\uff0c\u5b8c\u6210\u767b\u5f55\u6216 2FA \u540e\u81ea\u52a8\u4fdd\u5b58</span>';return}
    let h='<span class="ab-label">已保存:</span>';
    _acctCache.forEach(a=>{
      const initial=(a.username||a.email||'?')[0].toUpperCase();
      const label=a.username||a.email.split('@')[0];
      const cls=a.id===currentAcctId?'acct-chip active':'acct-chip';
      const hasTotp=a.totp?'✓ TOTP':'✗ 无TOTP';
      h+='<div class="'+cls+'" data-id="'+a.id+'" onclick="selectAccountById('+a.id+')" title="'+a.email+' ('+hasTotp+')">';
      h+='<span class="ac-avatar">'+initial+'</span>';
      h+='<span class="ac-name">'+label+'</span>';
      if(a.username)h+='<span class="ac-user">@'+a.username+'</span>';
      h+='</div>';
    });
    bar.innerHTML=h;
  }catch(e){}
}
function selectAccountById(id){
  const a=_acctCache.find(x=>x.id===id);
  if(!a)return;
  currentAcctId=id;
  document.getElementById('inp').value=a.email+'----'+a.password+(a.totp?'----'+a.totp:'');
  loadAccounts();
  ss('ok','已切换到: '+(a.username||a.email));
}
document.addEventListener('DOMContentLoaded',loadAccounts);

async function delRecord(table,id){
  if(!confirm('确定删除？'))return;
  await fetch('/api/history/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table:table,id:id})});
  loadHistory();
  if(table==='accounts'){loadAccounts();if(currentAcctId===id)currentAcctId=null}
}

async function exportJson(type){
  window.open('/api/export?type='+type,'_blank');
}
</script>
</body>
</html>
"""


# ─── API 路由 ─────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/go", methods=["POST"])
def api_go():
    """智能入口：2 段走 2FA 流程，3 段走登录验证"""
    data = request.get_json()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    totp = data.get("totp", "").strip()
    mode = data.get("mode", "login")

    if mode == "2fa" and not totp:
        # 2 段模式：走 2FA 开启流程
        logs = []
        logs.append(f"检测到 2 段输入（账----密），进入 2FA 自动开启流程")
        logs.append(f"账号: {email}")

        # 先用 HTTP 登录看看是否已有 2FA
        logs.append("[1/2] 尝试登录检测 2FA 状态...")
        try:
            session = _new_session()
            resp = session.get("https://github.com/login", timeout=15)
            resp.raise_for_status()
            csrf = _extract_csrf(resp.text)
            resp = session.post(
                "https://github.com/session",
                data={
                    "commit": "Sign in", "authenticity_token": csrf,
                    "login": email, "password": password,
                    "webauthn-conditional": "undefined",
                    "javascript-support": "true",
                    "webauthn-support": "supported",
                    "webauthn-iuvpaa-support": "unsupported",
                    "return_to": "", "timestamp": "", "timestamp_secret": "",
                },
                timeout=15, allow_redirects=True,
            )
            resp.raise_for_status()

            if "/sessions/two-factor" in resp.url:
                logs.append("  该账号已开启 2FA，请使用 3 段格式输入（账----密----TOTP）")
                return jsonify({"success": False, "error": "已有 2FA，请用 3 段格式", "logs": logs})

            if "github.com/login" in resp.url and "verified-device" not in resp.url:
                logs.append("  登录失败，请检查账号密码")
                return jsonify({"success": False, "error": "账号或密码错误", "logs": logs})

            logs.append("  登录成功，该账号尚未开启 2FA")
        except Exception as e:
            logs.append(f"  登录检测出错: {e}")

        # 调用 Playwright 开启 2FA
        logs.append("[2/2] 调用 Playwright 自动开启 2FA...")
        try:
            from github_2fa_setup import github_enable_2fa, AlreadyEnabled, LoginFailed, DeviceCodeNeeded, DeviceCodeInvalid
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                totp_secret, recovery_codes, username = loop.run_until_complete(
                    github_enable_2fa(email, password)
                )
            finally:
                loop.close()

            credential = f"{email}----{password}----{totp_secret}"
            logs.append(f"  2FA 开启成功!")
            logs.append(f"  用户名: {username}")
            logs.append(f"  TOTP Secret: {totp_secret}")
            logs.append(f"  恢复码: {len(recovery_codes)} 个")
            logs.append(f"  完整凭据: {credential}")

            recovery_str = "\n".join(recovery_codes) if recovery_codes else ""
            aid = _save_account(email, password, totp_secret, username, recovery_str)

            return jsonify({
                "success": True, "username": username,
                "credential": credential,
                "totp_secret": totp_secret,
                "recovery_codes": recovery_codes,
                "logs": logs,
            })
        except AlreadyEnabled as e:
            logs.append(f"  {e}")
            logs.append("  请使用 3 段格式输入（账----密----TOTP）")
            return jsonify({"success": False, "error": str(e), "logs": logs})
        except DeviceCodeNeeded:
            logs.append("  需要设备验证码，请查看邮箱获取验证码")
            logs.append("  暂不支持在 Web 界面输入设备验证码，请使用命令行:")
            logs.append(f"  python3 github_2fa_setup.py <验证码>")
            return jsonify({"success": False, "error": "需要设备验证码（查看邮箱）", "logs": logs})
        except (LoginFailed, DeviceCodeInvalid) as e:
            logs.append(f"  失败: {e}")
            return jsonify({"success": False, "error": str(e), "logs": logs})
        except ImportError:
            logs.append("  错误: 未安装 playwright，请执行 pip install playwright && playwright install chromium")
            return jsonify({"success": False, "error": "playwright 未安装", "logs": logs})
        except Exception as e:
            logs.append(f"  错误: {e}")
            return jsonify({"success": False, "error": str(e), "logs": logs})

    else:
        # 3 段模式：直接登录验证
        success, username, totp_code, logs = do_login(email, password, totp)
        if success:
            aid = _save_account(email, password, totp, username)
            credential = f"{email}----{password}----{totp}"
            return jsonify({
                "success": True, "username": username,
                "credential": credential, "totp_code": totp_code,
                "logs": logs,
            })
        else:
            return jsonify({"success": False, "error": "登录失败", "logs": logs})


@app.route("/api/pat", methods=["POST"])
def api_pat():
    """创建 PAT（Playwright 浏览器自动化）"""
    data = request.get_json()
    email = data.get("email", "")
    password = data.get("password", "")
    totp = data.get("totp", "")
    pat_name = data.get("name", "") or f"Auto-PAT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    days = data.get("days", 365)
    repo_access = data.get("repo_access", "all")

    logs = []
    try:
        from github_create_pat import (
            login_github, navigate_to_pat_creation,
            fill_pat_form, generate_token, generate_totp_code,
            DEFAULT_PERMISSIONS,
        )
        import github_create_pat
        from playwright.sync_api import sync_playwright

        logs.append("[开始] 调用 Playwright 创建 PAT...")
        logs.append(f"  Token 名称: {pat_name}")
        logs.append(f"  过期天数: {days}")
        logs.append(f"  仓库范围: {repo_access}")

        # 设置 args_global 供 navigate_to_pat_creation 的 sudo 模式使用
        class _Args:
            pass
        _a = _Args()
        _a.totp_secret = totp
        _a.headless = True
        github_create_pat.args_global = _a

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=UA,
            )
            page = context.new_page()
            try:
                logs.append("[1/4] 登录 GitHub...")
                login_github(page, email, password, totp)
                logs.append("  → 登录成功")

                logs.append("[2/4] 导航到 PAT 创建页面...")
                page = navigate_to_pat_creation(page, context)
                logs.append("  → 已到达 PAT 创建页面")

                logs.append("[3/4] 填写 PAT 表单...")
                fill_pat_form(
                    page,
                    token_name=pat_name,
                    description="Auto-generated PAT",
                    expiration_days=days,
                    repo_access=repo_access,
                    permissions=DEFAULT_PERMISSIONS.copy(),
                )
                logs.append("  → 表单填写完成")

                logs.append("[4/4] 生成 Token...")
                token = generate_token(page)
                logs.append(f"  → Token: {token}")

                if token:
                    aid = _get_account_id(email) or _save_account(email, password, totp)
                    _save_pat(aid, token, pat_name, "", f"{days}天")
                    return jsonify({"success": True, "token": token, "logs": logs})
                else:
                    return jsonify({"success": False, "error": "未能提取 Token", "logs": logs})
            finally:
                browser.close()

    except ImportError as e:
        logs.append(f"错误: 缺少依赖 — {e}")
        logs.append("请执行: pip install playwright pyotp && playwright install chromium")
        return jsonify({"success": False, "error": "playwright 未安装", "logs": logs})
    except Exception as e:
        logs.append(f"错误: {e}")
        return jsonify({"success": False, "error": str(e), "logs": logs})


@app.route("/api/repo", methods=["POST"])
def api_repo():
    """仓库操作（API）"""
    data = request.get_json()
    action = data.get("action", "list")
    token = data.get("token", "")
    headers = _api_headers(token)
    logs = []

    try:
        # 获取用户名
        r = req_lib.get(f"{API_BASE}/user", headers=headers, timeout=15)
        if r.status_code != 200:
            return jsonify({"success": False, "error": "PAT 无效或过期", "logs": ["PAT 验证失败"]})
        owner = r.json()["login"]
        logs.append(f"用户: {owner}")
        aid = _get_account_id(owner)

        if action == "list":
            repos = []
            page_num = 1
            while True:
                r = req_lib.get(f"{API_BASE}/user/repos?per_page=100&page={page_num}", headers=headers, timeout=15)
                batch = r.json()
                if not batch:
                    break
                for repo in batch:
                    repos.append({
                        "full_name": repo["full_name"],
                        "visibility": "private" if repo["private"] else "public",
                        "created": repo["created_at"][:10],
                        "url": repo["html_url"],
                    })
                page_num += 1
                if len(batch) < 100:
                    break
            logs.append(f"共 {len(repos)} 个仓库")
            return jsonify({"success": True, "repos": repos, "logs": logs})

        elif action == "delete":
            name = data.get("name", "")
            r = req_lib.delete(f"{API_BASE}/repos/{name}", headers=headers, timeout=15)
            if r.status_code == 204:
                logs.append(f"删除成功: {name}")
                return jsonify({"success": True, "logs": logs})
            else:
                logs.append(f"删除失败: {r.status_code} {r.text[:100]}")
                return jsonify({"success": False, "logs": logs})

        elif action == "create":
            import random, string
            pattern = data.get("pattern", "repo-{rand}")
            count = data.get("count", 1)
            visibility = data.get("visibility", "public")
            description = data.get("description", "")
            init_content = data.get("init_content", "")

            created = []
            for i in range(1, count + 1):
                name = pattern
                if "{rand}" in name:
                    name = name.replace("{rand}", "".join(random.choices(string.ascii_lowercase + string.digits, k=8)))
                if "{i}" in name:
                    name = name.replace("{i}", str(i))

                body = {"name": name, "description": description,
                        "private": visibility == "private", "auto_init": True,
                        "license_template": "mit"}
                r = req_lib.post(f"{API_BASE}/user/repos", headers=headers, json=body, timeout=15)
                if r.status_code in (200, 201):
                    info = r.json()
                    logs.append(f"  [{i}/{count}] {info['full_name']} ({visibility})")
                    created.append(info)
                    if aid:
                        _save_repo(aid, name, info["full_name"], info["html_url"], visibility)
                else:
                    logs.append(f"  [{i}/{count}] 失败: {r.status_code} {r.text[:80]}")
                if i < count:
                    time.sleep(1)

            # init content
            if init_content and created:
                logs.append("初始化 README...")
                for info in created:
                    readme = f"# {info['name']}\n\n{init_content}\n"
                    encoded = base64.b64encode(readme.encode()).decode()
                    api_path = f"/repos/{owner}/{info['name']}/contents/README.md"
                    sha = None
                    try:
                        existing = req_lib.get(f"{API_BASE}{api_path}?ref=main", headers=headers, timeout=15)
                        if existing.status_code == 200:
                            sha = existing.json()["sha"]
                    except Exception:
                        pass
                    put_data = {"message": "Initialize README.md", "content": encoded, "branch": "main"}
                    if sha:
                        put_data["sha"] = sha
                    req_lib.put(f"{API_BASE}{api_path}", headers=headers, json=put_data, timeout=15)

            logs.append(f"完成: 成功创建 {len(created)}/{count} 个仓库")
            return jsonify({"success": True, "message": f"创建 {len(created)}/{count} 个仓库", "logs": logs})

    except Exception as e:
        logs.append(f"错误: {e}")
        return jsonify({"success": False, "error": str(e), "logs": logs})


@app.route("/api/devin", methods=["POST"])
def api_devin():
    """Devin 注册/登录（Playwright）"""
    data = request.get_json()
    email = data.get("email", "")
    password = data.get("password", "")
    totp = data.get("totp", "")
    mode = data.get("mode", "signup")

    logs = []
    try:
        from devin_signup import devin_auth
        label = "注册" if mode == "signup" else "登录"
        logs.append(f"[开始] Devin {label}...")
        result = devin_auth(
            email=email, password=password, totp_secret=totp,
            signup=(mode == "signup"), headless=True,
        )
        url = result.get("url", "")
        aid = _get_account_id(email) or _save_account(email, password, totp)
        _save_devin(aid, mode, url, "成功" if result.get("success") else "失败")
        if result.get("success"):
            logs.append(f"完成! URL: {url}")
            return jsonify({"success": True, "url": url, "logs": logs})
        else:
            logs.append(f"失败")
            return jsonify({"success": False, "error": "Devin 操作未成功", "logs": logs})
    except ImportError as e:
        logs.append(f"错误: 缺少依赖 — {e}")
        logs.append("请执行: pip install playwright pyotp && playwright install chromium")
        return jsonify({"success": False, "error": "playwright 未安装", "logs": logs})
    except Exception as e:
        logs.append(f"错误: {e}")
        return jsonify({"success": False, "error": str(e), "logs": logs})


@app.route("/api/accounts")
def api_accounts():
    """获取已保存的账号列表（用于账号选择器）"""
    with _db() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT id, email, password, username, totp, updated FROM accounts ORDER BY updated DESC"
        ).fetchall()]
    return jsonify({"accounts": accounts})


@app.route("/api/history")
def api_history():
    """获取历史记录"""
    with _db() as conn:
        accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY updated DESC").fetchall()]
        pats = [dict(r) for r in conn.execute("SELECT * FROM pats ORDER BY created DESC").fetchall()]
        repos = [dict(r) for r in conn.execute("SELECT * FROM repos ORDER BY created DESC").fetchall()]
        devin = [dict(r) for r in conn.execute("SELECT * FROM devin ORDER BY created DESC").fetchall()]
    return jsonify({"accounts": accounts, "pats": pats, "repos": repos, "devin": devin})


@app.route("/api/history/delete", methods=["POST"])
def api_history_delete():
    """删除一条历史记录"""
    data = request.get_json()
    table = data.get("table", "")
    rid = data.get("id", 0)
    if table not in ("accounts", "pats", "repos", "devin"):
        return jsonify({"success": False, "error": "无效表名"})
    with _db() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
    return jsonify({"success": True})


@app.route("/api/export")
def api_export():
    """导出 JSON"""
    export_type = request.args.get("type", "all")
    with _db() as conn:
        result = {}
        if export_type in ("accounts", "all"):
            result["accounts"] = [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY updated DESC").fetchall()]
        if export_type in ("pats", "all"):
            result["pats"] = [dict(r) for r in conn.execute("SELECT * FROM pats ORDER BY created DESC").fetchall()]
        if export_type in ("repos", "all"):
            result["repos"] = [dict(r) for r in conn.execute("SELECT * FROM repos ORDER BY created DESC").fetchall()]
        if export_type in ("devin", "all"):
            result["devin"] = [dict(r) for r in conn.execute("SELECT * FROM devin ORDER BY created DESC").fetchall()]

    json_str = json.dumps(result, ensure_ascii=False, indent=2)
    filename = f"github_toolbox_{export_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        json_str,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── 主入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  GitHub & Devin 自动化工具箱 — Web GUI")
    print("  打开浏览器访问: http://localhost:5000")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False)
