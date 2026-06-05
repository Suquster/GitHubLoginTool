#!/usr/bin/env python3
"""
GitHub Fine-grained PAT 自动创建工具（HTTP 版 — 极速）
=====================================================
纯 HTTP 请求实现，无需浏览器，约 2-3 秒即可完成。

依赖: pip install requests pyotp

用法:
  python github_create_pat_fast.py --email EMAIL --password PASSWORD --totp-secret SECRET
  python github_create_pat_fast.py  # 交互式输入

可选参数:
  --token-name NAME       Token 名称（默认: Auto-PAT-<timestamp>）
  --expiration DAYS       过期天数（默认: 365，即 1 年）
  --description DESC      Token 描述
  --repo-access ACCESS    仓库范围: all / public（默认: all）
  --permissions PERMS     权限 JSON，如 '{"contents":"write","issues":"read"}'
  --quiet                 静默模式，仅输出 Token 值
"""

import argparse
import getpass
import json
import re
import sys
import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("错误: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    import pyotp
except ImportError:
    print("错误: pip install pyotp", file=sys.stderr)
    sys.exit(1)


# ── 默认权限 ─────────────────────────────────────────────────
DEFAULT_PERMISSIONS = {
    "administration": "write",
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "actions": "read",
    "deployments": "read",
    "metadata": "read",
}

GITHUB_BASE = "https://github.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ── 工具函数 ─────────────────────────────────────────────────

def _extract_auth_token(html: str) -> str:
    """从 HTML 中提取 authenticity_token"""
    m = re.search(r'name="authenticity_token" value="([^"]+)"', html)
    if not m:
        raise RuntimeError("无法提取 authenticity_token")
    return m.group(1)


def _totp_code(secret: str) -> str:
    """从 TOTP 密钥生成 6 位验证码"""
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).now()


# ── 核心流程 ─────────────────────────────────────────────────

def login(session: requests.Session, email: str, password: str,
          totp_secret: str, quiet: bool = False) -> None:
    """登录 GitHub（邮箱 + 密码 + TOTP 2FA）"""
    if not quiet:
        print("[1/3] 登录 GitHub...")

    r = session.get(f"{GITHUB_BASE}/login")
    auth_token = _extract_auth_token(r.text)

    r = session.post(f"{GITHUB_BASE}/session", data={
        "authenticity_token": auth_token,
        "login": email,
        "password": password,
        "webauthn-conditional": "undefined",
        "javascript-support": "true",
        "webauthn-support": "unknown",
        "webauthn-iuvpaa-support": "unknown",
        "return_to": "",
        "commit": "Sign in",
    }, allow_redirects=True)

    # 检查密码是否正确
    if "Incorrect username or password" in r.text:
        raise RuntimeError("登录失败: 用户名或密码错误")

    # 2FA
    if "two-factor" in r.url:
        if not quiet:
            print("  → 两步验证...")
        auth_2fa = _extract_auth_token(r.text)
        code = _totp_code(totp_secret)
        r = session.post(f"{GITHUB_BASE}/sessions/two-factor", data={
            "authenticity_token": auth_2fa,
            "app_otp": code,
        }, allow_redirects=True)

        if "two-factor" in r.url:
            # TOTP 可能刚好在切换窗口期，等待下一个周期再试一次
            if not quiet:
                print("  → TOTP 验证码过期，等待下一个周期...")
            time.sleep(31)
            auth_2fa = _extract_auth_token(r.text)
            code = _totp_code(totp_secret)
            r = session.post(f"{GITHUB_BASE}/sessions/two-factor", data={
                "authenticity_token": auth_2fa,
                "app_otp": code,
            }, allow_redirects=True)
            if "two-factor" in r.url:
                raise RuntimeError("登录失败: TOTP 验证码错误")

    # 验证登录
    if "login" in r.url:
        raise RuntimeError("登录失败")

    if not quiet:
        print("  → 登录成功")


def create_pat(session: requests.Session, token_name: str, description: str,
               expiration_days: int, repo_access: str, permissions: dict,
               totp_secret: str, quiet: bool = False) -> dict:
    """
    创建 Fine-grained PAT，返回 {"token": "github_pat_...", "name": ..., "expires": ...}
    """
    if not quiet:
        print("[2/3] 获取 PAT 表单...")

    # 获取表单页面
    r = session.get(f"{GITHUB_BASE}/settings/personal-access-tokens/new")

    # 处理 sudo 模式
    if "confirm_access" in r.url or "sudo" in r.url:
        if not quiet:
            print("  → sudo 模式确认...")
        auth_sudo = _extract_auth_token(r.text)
        code = _totp_code(totp_secret)
        r = session.post(r.url, data={
            "authenticity_token": auth_sudo,
            "sudo_otp": code,
        }, allow_redirects=True)

        if "personal-access-tokens" not in r.url:
            raise RuntimeError("sudo 模式验证失败")

    # 从表单中提取所有隐藏字段
    pat_form = re.search(
        r'<form[^>]*action="/settings/personal-access-tokens"[^>]*method="post"[^>]*>(.*?)</form>',
        r.text, re.DOTALL,
    )
    if not pat_form:
        raise RuntimeError("未找到 PAT 创建表单")

    fields = dict(re.findall(
        r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*?)"',
        pat_form.group(1),
    ))

    # 填写表单
    fields["user_programmatic_access[name]"] = token_name
    fields["user_programmatic_access[description]"] = description
    fields["install_target"] = "all" if repo_access == "all" else "none"
    fields["confirm"] = "1"  # 跳过确认对话框，直接创建

    # 设置过期日期
    expires_at = (datetime.now() + timedelta(days=expiration_days)).strftime("%Y-%m-%d")
    fields["user_programmatic_access[custom_expires_at]"] = expires_at

    # 设置权限
    for key in list(fields.keys()):
        if key.startswith("integration[default_permissions]"):
            perm_name = key.split("[")[-1].rstrip("]")
            fields[key] = permissions.get(perm_name, "none")

    if not quiet:
        print("[3/3] 创建 Token...")

    # 提交表单
    r = session.post(
        f"{GITHUB_BASE}/settings/personal-access-tokens",
        data=fields,
        allow_redirects=True,
    )

    if r.status_code != 200:
        raise RuntimeError(f"创建失败: HTTP {r.status_code}")

    # 提取 Token
    token_match = re.search(r"github_pat_[A-Za-z0-9_]{30,}", r.text)
    if not token_match:
        # 检查是否有错误消息
        flash_err = re.search(r'class="flash-error[^"]*"[^>]*>([^<]+)', r.text)
        if flash_err:
            raise RuntimeError(f"创建失败: {flash_err.group(1).strip()}")
        raise RuntimeError("创建失败: 无法提取 Token 值")

    token_value = token_match.group(0)

    if not quiet:
        print("  → Token 生成成功！")

    return {
        "token": token_value,
        "name": token_name,
        "expires": expires_at,
        "repo_access": repo_access,
        "permissions": permissions,
    }


# ── 入口 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GitHub Fine-grained PAT 自动创建工具（HTTP 极速版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 命令行参数
  python github_create_pat_fast.py --email user@email.com --password pw --totp-secret SECRET

  # 交互式
  python github_create_pat_fast.py

  # 静默模式（仅输出 Token，适合管道）
  python github_create_pat_fast.py --email user@email.com --password pw --totp-secret SECRET --quiet

  # 自定义权限
  python github_create_pat_fast.py ... --permissions '{"contents":"write","issues":"read"}'
        """,
    )
    parser.add_argument("--email", help="GitHub 登录邮箱")
    parser.add_argument("--password", help="GitHub 登录密码")
    parser.add_argument("--totp-secret", help="TOTP 密钥（Base32 字符串）")
    parser.add_argument("--token-name", default=None, help="Token 名称")
    parser.add_argument("--expiration", type=int, default=365, help="过期天数（默认 365）")
    parser.add_argument("--description", default="Auto-generated PAT", help="描述")
    parser.add_argument("--repo-access", choices=["all", "public"], default="all",
                        help="仓库范围（默认 all）")
    parser.add_argument("--permissions", default=None,
                        help='权限 JSON 字符串')
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式，仅输出 Token")

    args = parser.parse_args()

    # 交互式补充参数
    if not args.email:
        args.email = input("GitHub 邮箱: ").strip()
    if not args.password:
        args.password = getpass.getpass("GitHub 密码: ")
    if not args.totp_secret:
        args.totp_secret = getpass.getpass("TOTP 密钥: ")

    if not args.token_name:
        args.token_name = f"Auto-PAT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 解析权限
    if args.permissions:
        try:
            permissions = json.loads(args.permissions)
        except json.JSONDecodeError:
            print("错误: --permissions 必须是有效 JSON", file=sys.stderr)
            sys.exit(1)
    else:
        permissions = DEFAULT_PERMISSIONS.copy()

    # 配置摘要
    if not args.quiet:
        print("=" * 50)
        print("GitHub PAT 创建工具（HTTP 极速版）")
        print("=" * 50)
        print(f"  邮箱:     {args.email}")
        print(f"  Token名:  {args.token_name}")
        print(f"  过期:     {args.expiration} 天")
        print(f"  仓库范围: {args.repo_access}")
        print(f"  权限:")
        for name, level in permissions.items():
            lvl = "Read and write" if level == "write" else "Read-only"
            print(f"    - {name}: {lvl}")
        print("=" * 50)
        print()

    # 执行
    t0 = time.time()
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    try:
        login(session, args.email, args.password, args.totp_secret, args.quiet)
        result = create_pat(
            session,
            token_name=args.token_name,
            description=args.description,
            expiration_days=args.expiration,
            repo_access=args.repo_access,
            permissions=permissions,
            totp_secret=args.totp_secret,
            quiet=args.quiet,
        )
    except RuntimeError as e:
        if args.quiet:
            print(str(e), file=sys.stderr)
        else:
            print(f"\n错误: {e}")
        sys.exit(1)

    elapsed = time.time() - t0

    if args.quiet:
        # 静默模式: 只输出 token
        print(result["token"])
    else:
        print()
        print("=" * 50)
        print("PAT 创建成功！")
        print("=" * 50)
        print(f"  Token 名称:  {result['name']}")
        print(f"  过期时间:    {result['expires']}")
        print(f"  仓库范围:    {result['repo_access']}")
        print(f"  权限数:      {len(result['permissions'])} 项")
        print()
        print(f"  Token 值:")
        print(f"  {result['token']}")
        print()
        print(f"  耗时: {elapsed:.1f} 秒")
        print("=" * 50)

    return result


if __name__ == "__main__":
    main()
