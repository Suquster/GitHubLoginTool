"""
GitHub 自动化登录脚本 (Python + requests)
支持：邮箱/用户名 + 密码 + TOTP 两步验证
"""

import re
import sys
import json
import requests


def get_totp_code(totp_secret: str, api_url: str = "https://kloping.top/api/2fa0") -> str:
    """通过在线 API 获取 TOTP 验证码"""
    resp = requests.get(f"{api_url}?secret={totp_secret}", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", "")
    if not code:
        raise ValueError(f"获取 TOTP 验证码失败: {data}")
    return code


def extract_token(html: str, field_name: str = "authenticity_token") -> str:
    """从 HTML 中提取 CSRF token"""
    pattern = rf'name="{field_name}"\s+value="([^"]+)"'
    match = re.search(pattern, html)
    if not match:
        raise ValueError(f"无法提取 {field_name}")
    return match.group(1)


def github_login(username: str, password: str, totp_secret: str) -> requests.Session:
    """
    自动登录 GitHub，返回已认证的 Session。

    参数:
        username:    GitHub 用户名或邮箱
        password:    密码
        totp_secret: TOTP 密钥 (16位)

    返回:
        已登录的 requests.Session 对象
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })

    # ---- 第1步: 获取登录页面 + CSRF token ----
    print("[1/4] 获取登录页面...")
    resp = session.get("https://github.com/login", timeout=15)
    resp.raise_for_status()
    token = extract_token(resp.text)
    print(f"      CSRF token: {token[:20]}...")

    # ---- 第2步: 提交用户名 + 密码 ----
    print("[2/4] 提交登录凭据...")
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
    resp = session.post(
        "https://github.com/session",
        data=login_data,
        timeout=15,
        allow_redirects=True,
    )
    resp.raise_for_status()

    # ---- 第3步: 处理 2FA ----
    if "/sessions/two-factor" in resp.url:
        print("[3/4] 需要两步验证，获取 TOTP 验证码...")
        totp_code = get_totp_code(totp_secret)
        print(f"      TOTP 验证码: {totp_code}")

        # 提取 2FA 页面的 CSRF token
        token_2fa = extract_token(resp.text)

        resp = session.post(
            "https://github.com/sessions/two-factor",
            data={
                "authenticity_token": token_2fa,
                "app_otp": totp_code,
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
    else:
        print("[3/4] 无需两步验证，跳过")

    # ---- 第4步: 验证登录状态 ----
    print("[4/4] 验证登录状态...")
    if "github.com/login" in resp.url:
        raise RuntimeError("登录失败: 被重定向回登录页面")

    # 通过访问 settings 页面确认
    check = session.get("https://github.com/settings/profile", timeout=15)
    if check.status_code == 200 and "Public profile" in check.text:
        # 提取用户名
        match = re.search(r'"login":"([^"]+)"', check.text)
        logged_user = match.group(1) if match else "未知"
        print(f"      登录成功! 当前用户: {logged_user}")
    else:
        raise RuntimeError(f"登录验证失败: status={check.status_code}, url={check.url}")

    return session


def demo_actions(session: requests.Session):
    """演示登录后的操作"""
    print("\n===== 登录后操作演示 =====\n")

    # 获取用户信息
    print("[演示1] 获取用户仓库列表...")
    resp = session.get("https://api.github.com/user/repos?per_page=5",
                       headers={"Accept": "application/vnd.github.v3+json"},
                       timeout=15)
    if resp.status_code == 200:
        repos = resp.json()
        for r in repos:
            print(f"  - {r['full_name']} (created: {r['created_at']})")
    else:
        # 用网页方式获取
        resp = session.get("https://github.com/settings/repositories", timeout=15)
        repo_names = re.findall(r'href="/([^"]+/[^"]+)" data-pjax', resp.text)
        for name in repo_names[:5]:
            print(f"  - {name}")

    # 获取 Security log
    print("\n[演示2] 获取 Security Log (最近5条)...")
    resp = session.get("https://github.com/settings/security-log", timeout=15)
    if resp.status_code == 200:
        events = re.findall(
            r'action%3A([^"]+)"[^>]*>([^<]+)</a>\s*([^<]*?)(?:<a[^>]*>([^<]*)</a>)?',
            resp.text
        )
        for i, ev in enumerate(events[:5]):
            action = ev[1].strip()
            detail = ev[2].strip()
            ip = ev[3].strip() if len(ev) > 3 else ""
            print(f"  {i+1}. {action} - {detail} {ip}")
    print("\n===== 演示结束 =====")


def batch_login(accounts: list[dict]) -> list[dict]:
    """
    批量登录多个 GitHub 账号。

    参数:
        accounts: 账号列表, 每个元素为 {"username": ..., "password": ..., "totp_secret": ...}

    返回:
        结果列表, 每个元素为 {"username": ..., "success": bool, "session": Session|None, "error": str|None}
    """
    results = []
    for i, acc in enumerate(accounts):
        print(f"\n{'='*50}")
        print(f"[账号 {i+1}/{len(accounts)}] {acc['username']}")
        print('='*50)
        try:
            session = github_login(
                username=acc["username"],
                password=acc["password"],
                totp_secret=acc["totp_secret"],
            )
            results.append({
                "username": acc["username"],
                "success": True,
                "session": session,
                "error": None,
            })
        except Exception as e:
            print(f"  登录失败: {e}")
            results.append({
                "username": acc["username"],
                "success": False,
                "session": None,
                "error": str(e),
            })
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GitHub 自动化登录工具")
    parser.add_argument("-u", "--username", help="GitHub 用户名或邮箱")
    parser.add_argument("-p", "--password", help="密码")
    parser.add_argument("-t", "--totp-secret", help="TOTP 密钥 (16位)")
    parser.add_argument("-f", "--file", help="批量账号 JSON 文件路径")
    parser.add_argument("--demo", action="store_true", help="登录成功后运行演示操作")
    args = parser.parse_args()

    # ---- 模式1: 单账号登录 ----
    if args.username:
        if not args.password or not args.totp_secret:
            parser.error("单账号模式需要同时提供 -p 和 -t 参数")
        try:
            session = github_login(args.username, args.password, args.totp_secret)
            if args.demo:
                demo_actions(session)
            print("\n登录成功!")
        except Exception as e:
            print(f"\n错误: {e}", file=sys.stderr)
            sys.exit(1)

    # ---- 模式2: 批量登录 (JSON 文件) ----
    elif args.file:
        # JSON 格式: [{"username": "...", "password": "...", "totp_secret": "..."}, ...]
        with open(args.file, "r") as f:
            accounts = json.load(f)
        results = batch_login(accounts)

        # 打印汇总
        print(f"\n{'='*50}")
        print("批量登录汇总:")
        print(f"{'='*50}")
        success = sum(1 for r in results if r["success"])
        fail = sum(1 for r in results if not r["success"])
        print(f"  成功: {success}")
        print(f"  失败: {fail}")
        print(f"  总计: {len(results)}")
        if fail > 0:
            print("\n失败账号:")
            for r in results:
                if not r["success"]:
                    print(f"  - {r['username']}: {r['error']}")

    # ---- 无参数: 显示用法 ----
    else:
        parser.print_help()
        print("\n使用示例:")
        print("  # 单账号登录:")
        print('  python3 github_auto_login.py -u "user@gmail.com" -p "password" -t "TOTP_SECRET"')
        print()
        print("  # 单账号登录 + 演示操作:")
        print('  python3 github_auto_login.py -u "user@gmail.com" -p "password" -t "TOTP_SECRET" --demo')
        print()
        print("  # 批量登录 (JSON 文件):")
        print("  python3 github_auto_login.py -f accounts.json")
        print()
        print("  # accounts.json 格式:")
        print('  [{"username": "a@gmail.com", "password": "pwd1", "totp_secret": "SECRET1"}, ...]')
