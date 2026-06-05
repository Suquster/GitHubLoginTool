"""
GitHub 自动化登录工具 - Web GUI 版本
在浏览器中打开 http://localhost:5000 即可使用
依赖：pip install requests flask
"""

import re
import json
from flask import Flask, render_template_string, request, jsonify
import requests as req_lib

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GitHub 自动登录工具</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 40px 20px;
        }
        .container {
            width: 100%;
            max-width: 500px;
        }
        .header {
            text-align: center;
            margin-bottom: 24px;
        }
        .header h1 {
            font-size: 24px;
            color: #f0f6fc;
            margin-bottom: 6px;
        }
        .header p {
            font-size: 13px;
            color: #8b949e;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 20px;
            margin-bottom: 16px;
        }
        .card h2 {
            font-size: 14px;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #21262d;
        }
        .form-group {
            margin-bottom: 14px;
        }
        .form-group label {
            display: block;
            font-size: 13px;
            color: #c9d1d9;
            margin-bottom: 6px;
            font-weight: 500;
        }
        .form-group input {
            width: 100%;
            padding: 8px 12px;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #c9d1d9;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }
        .form-group input:focus {
            border-color: #58a6ff;
        }
        .form-group input::placeholder {
            color: #484f58;
        }
        .btn-row {
            display: flex;
            gap: 10px;
            margin-top: 18px;
        }
        .btn {
            flex: 1;
            padding: 10px 16px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: #238636;
            color: #fff;
        }
        .btn-primary:hover { background: #2ea043; }
        .btn-primary:disabled {
            background: #21262d;
            color: #484f58;
            cursor: not-allowed;
        }
        .btn-secondary {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
        }
        .btn-secondary:hover { background: #30363d; }
        .status-bar {
            padding: 10px 14px;
            border-radius: 6px;
            font-size: 13px;
            margin-bottom: 12px;
            display: none;
        }
        .status-success {
            display: block;
            background: #0d2818;
            border: 1px solid #238636;
            color: #3fb950;
        }
        .status-error {
            display: block;
            background: #2d1117;
            border: 1px solid #da3633;
            color: #f85149;
        }
        .status-loading {
            display: block;
            background: #1a1e24;
            border: 1px solid #30363d;
            color: #8b949e;
        }
        .log-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 12px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 12px;
            line-height: 1.6;
            max-height: 250px;
            overflow-y: auto;
            white-space: pre-wrap;
            color: #8b949e;
        }
        .log-box .log-success { color: #3fb950; }
        .log-box .log-error { color: #f85149; }
        .log-box .log-info { color: #58a6ff; }
        .spinner {
            display: inline-block;
            width: 14px;
            height: 14px;
            border: 2px solid #484f58;
            border-top-color: #58a6ff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            vertical-align: middle;
            margin-right: 6px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .result-info {
            margin-top: 12px;
            padding: 12px;
            background: #0d2818;
            border: 1px solid #238636;
            border-radius: 6px;
        }
        .result-info table {
            width: 100%;
            font-size: 13px;
        }
        .result-info td {
            padding: 4px 0;
        }
        .result-info td:first-child {
            color: #8b949e;
            width: 100px;
        }
        .result-info td:last-child {
            color: #3fb950;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>GitHub 自动登录工具</h1>
            <p>格式: 账号----密码----TOTP密钥，支持多行批量</p>
        </div>

        <div id="statusBar" class="status-bar"></div>

        <div class="card">
            <h2>登录信息</h2>
            <div class="form-group">
                <label>输入账号信息（每行一个，用 ---- 分隔）</label>
                <textarea id="accountInput" rows="5" placeholder="账号----密码----TOTP密钥&#10;例如:&#10;walker1317670@gmail.com----t%%5VugTA1#K----X4TFJHJUNRTVJYVI"
                style="width:100%;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;font-family:Consolas,Monaco,monospace;outline:none;resize:vertical;"></textarea>
            </div>
            <div class="btn-row">
                <button class="btn btn-primary" id="loginBtn" onclick="doLogin()">登 录</button>
                <button class="btn btn-secondary" onclick="clearAll()">清 空</button>
            </div>
        </div>

        <div class="card">
            <h2>运行日志</h2>
            <div class="log-box" id="logBox">等待操作...</div>
        </div>

        <div id="resultBox"></div>
    </div>

    <script>
        function setStatus(type, msg) {
            const bar = document.getElementById('statusBar');
            bar.className = 'status-bar status-' + type;
            bar.innerHTML = (type === 'loading' ? '<span class="spinner"></span>' : '') + msg;
        }

        function addLog(msg, type) {
            const box = document.getElementById('logBox');
            const cls = type ? 'log-' + type : '';
            box.innerHTML += '<span class="' + cls + '">' + msg + '</span>\\n';
            box.scrollTop = box.scrollHeight;
        }

        function clearAll() {
            document.getElementById('accountInput').value = '';
            document.getElementById('logBox').innerHTML = '等待操作...';
            document.getElementById('statusBar').style.display = 'none';
            document.getElementById('resultBox').innerHTML = '';
        }

        async function doLogin() {
            const raw = document.getElementById('accountInput').value.trim();
            if (!raw) {
                setStatus('error', '请输入账号信息！');
                return;
            }

            // 按行分割，支持多行批量
            const lines = raw.split('\\n').map(l => l.trim()).filter(l => l);
            const accounts = [];
            for (let i = 0; i < lines.length; i++) {
                const parts = lines[i].split('----');
                if (parts.length !== 3) {
                    setStatus('error', '第 ' + (i+1) + ' 行格式错误，需要: 账号----密码----TOTP密钥');
                    return;
                }
                accounts.push({ username: parts[0].trim(), password: parts[1].trim(), totp_secret: parts[2].trim() });
            }

            const btn = document.getElementById('loginBtn');
            btn.disabled = true;
            document.getElementById('logBox').innerHTML = '';
            document.getElementById('resultBox').innerHTML = '';

            let successCount = 0, failCount = 0;
            let resultHtml = '';

            for (let i = 0; i < accounts.length; i++) {
                const acc = accounts[i];
                if (accounts.length > 1) {
                    addLog('\\n━━━ 账号 ' + (i+1) + '/' + accounts.length + ': ' + acc.username + ' ━━━', 'info');
                }
                setStatus('loading', '正在登录 (' + (i+1) + '/' + accounts.length + '): ' + acc.username);

                try {
                    const resp = await fetch('/api/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(acc)
                    });
                    const data = await resp.json();

                    data.logs.forEach(log => {
                        let type = '';
                        if (log.includes('成功') || log.includes('TOTP')) type = 'success';
                        else if (log.includes('错误') || log.includes('失败')) type = 'error';
                        else if (log.startsWith('[')) type = 'info';
                        addLog(log, type);
                    });

                    if (data.success) {
                        successCount++;
                        resultHtml += '<tr><td>' + acc.username + '</td><td style="color:#3fb950">成功 (' + data.username + ')</td><td>' + (data.totp_code||'-') + '</td></tr>';
                    } else {
                        failCount++;
                        resultHtml += '<tr><td>' + acc.username + '</td><td style="color:#f85149">失败: ' + data.error + '</td><td>-</td></tr>';
                    }
                } catch (e) {
                    failCount++;
                    addLog('网络错误: ' + e.message, 'error');
                    resultHtml += '<tr><td>' + acc.username + '</td><td style="color:#f85149">网络错误</td><td>-</td></tr>';
                }
            }

            // 汇总
            if (failCount === 0) {
                setStatus('success', '全部登录成功! 成功: ' + successCount);
            } else if (successCount === 0) {
                setStatus('error', '全部登录失败! 失败: ' + failCount);
            } else {
                setStatus('loading', '部分成功 — 成功: ' + successCount + ' / 失败: ' + failCount);
            }

            document.getElementById('resultBox').innerHTML = '<div class="result-info"><table style="width:100%;font-size:13px"><tr style="color:#8b949e"><td>账号</td><td>状态</td><td>TOTP码</td></tr>' + resultHtml + '</table></div>';

            btn.disabled = false;
        }

        // Enter 键触发登录
        document.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey && e.target.tagName !== 'TEXTAREA') doLogin();
        });
    </script>
</body>
</html>
"""


def get_totp_code(totp_secret, api_url="https://kloping.top/api/2fa0"):
    resp = req_lib.get(f"{api_url}?secret={totp_secret}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", "")
    if not code:
        raise ValueError(f"获取 TOTP 验证码失败: {data}")
    return code


def extract_token(html, field_name="authenticity_token"):
    pattern = rf'name="{field_name}"\s+value="([^"]+)"'
    match = re.search(pattern, html)
    if not match:
        raise ValueError(f"无法提取 {field_name}")
    return match.group(1)


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    username = data.get("username", "")
    password = data.get("password", "")
    totp_secret = data.get("totp_secret", "")

    logs = []
    totp_code = ""

    try:
        session = req_lib.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

        logs.append("[1/4] 获取登录页面...")
        resp = session.get("https://github.com/login", timeout=15)
        resp.raise_for_status()
        token = extract_token(resp.text)
        logs.append(f"      CSRF token: {token[:20]}...")

        logs.append("[2/4] 提交登录凭据...")
        login_data = {
            "commit": "Sign in",
            "authenticity_token": token,
            "login": username,
            "password": password,
            "webauthn-conditional": "undefined",
            "javascript-support": "true",
            "webauthn-support": "supported",
            "webauthn-iuvpaa-support": "unsupported",
            "return_to": "",
            "timestamp": "",
            "timestamp_secret": "",
        }
        resp = session.post("https://github.com/session", data=login_data, timeout=15, allow_redirects=True)
        resp.raise_for_status()

        if "/sessions/two-factor" in resp.url:
            logs.append("[3/4] 需要两步验证，获取 TOTP 验证码...")
            totp_code = get_totp_code(totp_secret)
            logs.append(f"      TOTP 验证码: {totp_code}")
            token_2fa = extract_token(resp.text)
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
            match = re.search(r'"login":"([^"]+)"', check.text)
            logged_user = match.group(1) if match else "未知"
            logs.append(f"      登录成功! 当前用户: {logged_user}")
            return jsonify({"success": True, "username": logged_user, "totp_code": totp_code, "logs": logs})
        else:
            raise RuntimeError(f"验证失败: status={check.status_code}")

    except Exception as e:
        logs.append(f"错误: {e}")
        return jsonify({"success": False, "error": str(e), "logs": logs})


if __name__ == "__main__":
    print("=" * 50)
    print("  GitHub 自动登录工具 - Web GUI")
    print("  打开浏览器访问: http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
