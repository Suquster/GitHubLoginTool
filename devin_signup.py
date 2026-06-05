#!/usr/bin/env python3
"""
Devin 自动注册/登录脚本（通过 GitHub OAuth）
=============================================
自动完成：GitHub 登录 → OAuth 授权 → 注册/登录 Devin

依赖: pip install playwright pyotp
      playwright install chromium

用法:
  # 注册 Devin（如果已有账号则自动登录）
  python devin_signup.py --email x --password x --totp-secret x

  # 仅登录
  python devin_signup.py --email x --password x --totp-secret x --signin

  # 无头模式（不弹浏览器）
  python devin_signup.py --email x --password x --totp-secret x --headless

  # 交互式输入
  python devin_signup.py
"""

import argparse
import getpass
import re
import sys
import time

try:
    import pyotp
except ImportError:
    print("错误: pip install pyotp", file=sys.stderr)
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("错误: pip install playwright && playwright install chromium", file=sys.stderr)
    sys.exit(1)


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def _totp_code(secret: str) -> str:
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).now()


def _totp_remaining(secret: str) -> int:
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).interval - int(time.time()) % pyotp.TOTP(clean).interval


def devin_auth(email: str, password: str, totp_secret: str,
               signup: bool = True, headless: bool = True,
               quiet: bool = False) -> dict:
    """
    通过 GitHub OAuth 注册或登录 Devin
    返回: {"success": bool, "mode": "signup"|"signin", "url": str, "org": str}
    """
    mode = "signup" if signup else "signin"
    start_url = "https://app.devin.ai/signup" if signup else "https://app.devin.ai/auth/login"

    if not quiet:
        print(f"[1/5] 启动浏览器... (模式: {'注册' if signup else '登录'})")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=UA,
        )
        page = ctx.new_page()

        try:
            # Step 1: Open Devin signup/login page
            if not quiet:
                print(f"[2/5] 打开 Devin {mode} 页面...")
            page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # Check if already logged in (redirected to dashboard)
            if "/org/" in page.url and "auth" not in page.url:
                if not quiet:
                    print("  → 已经登录！直接进入了 Devin 控制台")
                org = _extract_org(page.url)
                return {"success": True, "mode": "already_logged_in",
                        "url": page.url, "org": org}

            # Step 2: Click "Continue with GitHub"
            if not quiet:
                print("[3/5] 点击 'Continue with GitHub'...")

            gh_btn = page.query_selector('button:has-text("Continue with GitHub")')
            if not gh_btn:
                raise RuntimeError("找不到 'Continue with GitHub' 按钮")
            gh_btn.click()
            time.sleep(5)
            page.wait_for_load_state("domcontentloaded")

            # Step 3: Handle GitHub login (if needed)
            current = page.url
            if "github.com/login" in current:
                if not quiet:
                    print("[3/5] 登录 GitHub...")
                _github_login(page, email, password, totp_secret, quiet)
                time.sleep(5)
                page.wait_for_load_state("domcontentloaded")
                current = page.url

            # Step 4: Handle GitHub OAuth authorization (if needed)
            if "github.com/login/oauth/authorize" in current:
                if not quiet:
                    print("[4/5] 授权 Devin 应用...")
                _github_authorize(page, totp_secret, quiet)
                time.sleep(5)
                page.wait_for_load_state("domcontentloaded")
                current = page.url

            # Step 5: Handle Devin post-auth flow
            if not quiet:
                print("[5/5] 处理注册/登录后续...")

            # Wait for redirect back to Devin
            for _ in range(30):
                current = page.url
                if "app.devin.ai" in current:
                    break
                time.sleep(1)

            time.sleep(3)
            current = page.url

            # Handle upgrade/pricing page
            if "/auth/upgrade" in current or "/upgrade" in current:
                if not quiet:
                    print("  → 定价页面，选择免费版...")
                free_btn = page.query_selector('a:has-text("Continue with free")')
                if free_btn:
                    free_btn.click()
                    time.sleep(3)
                    page.wait_for_load_state("domcontentloaded")
                    current = page.url

            # Handle org setup / select-org page
            if "/select-org" in current or "/setup" in current:
                if not quiet:
                    print("  → 初始设置页面...")
                time.sleep(3)
                current = page.url

            # Check success
            if "/org/" in current:
                org = _extract_org(current)
                if not quiet:
                    print(f"\n  {'注册' if signup else '登录'}成功！")
                    print(f"  组织: {org}")
                    print(f"  URL: {current}")
                return {"success": True, "mode": mode, "url": current, "org": org}

            # Even if we're on some other Devin page, it might be successful
            if "app.devin.ai" in current and "auth" not in current:
                if not quiet:
                    print(f"\n  成功！当前页面: {current}")
                return {"success": True, "mode": mode, "url": current, "org": ""}

            raise RuntimeError(f"未能完成，停在: {current}")

        except Exception as e:
            # Take screenshot for debugging
            try:
                page.screenshot(path="/tmp/devin_auth_error.png")
            except Exception:
                pass
            raise RuntimeError(f"{'注册' if signup else '登录'}失败: {e}")
        finally:
            browser.close()


def _github_login(page, email: str, password: str, totp_secret: str, quiet: bool):
    """处理 GitHub 登录页面"""
    page.fill("#login_field", email)
    page.fill("#password", password)
    page.click('input[type="submit"]')
    time.sleep(4)
    page.wait_for_load_state("domcontentloaded")

    # Handle TOTP
    if "two-factor" in page.url:
        if not quiet:
            print("  → 两步验证...")

        # Check if TOTP code is about to expire
        remaining = _totp_remaining(totp_secret)
        if remaining < 5:
            if not quiet:
                print(f"  → TOTP 即将过期，等待 {remaining + 2} 秒...")
            time.sleep(remaining + 2)

        code = _totp_code(totp_secret)
        otp_input = page.query_selector('input[name="app_otp"]')
        if otp_input:
            otp_input.fill(code)
            time.sleep(4)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

    if "login" in page.url and "oauth" not in page.url:
        raise RuntimeError("GitHub 登录失败，请检查账号密码")

    if not quiet:
        print("  → GitHub 登录成功")


def _github_authorize(page, totp_secret: str, quiet: bool):
    """处理 GitHub OAuth 授权页面"""
    # Check for "Authorize" button
    auth_btn = page.query_selector('button[name="authorize"]:has-text("Authorize"), button#js-oauth-authorize-btn')
    if auth_btn:
        auth_btn.click()
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded")
        if not quiet:
            print("  → 已授权")
        return

    # Might need password/TOTP confirmation (sudo mode)
    sudo_otp = page.query_selector('input[name="sudo_otp"], input[name="otp"]')
    if sudo_otp:
        if not quiet:
            print("  → 需要验证身份...")
        remaining = _totp_remaining(totp_secret)
        if remaining < 5:
            time.sleep(remaining + 2)
        code = _totp_code(totp_secret)
        sudo_otp.fill(code)
        submit = page.query_selector('button[type="submit"]')
        if submit:
            submit.click()
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded")

        # Try authorize again
        auth_btn = page.query_selector('button[name="authorize"]')
        if auth_btn:
            auth_btn.click()
            time.sleep(5)

    if not quiet:
        print("  → 授权处理完成")


def _extract_org(url: str) -> str:
    m = re.search(r"/org/([^/\?]+)", url)
    return m.group(1) if m else ""


def main():
    parser = argparse.ArgumentParser(description="Devin 自动注册/登录（通过 GitHub OAuth）")
    parser.add_argument("--email", help="GitHub 邮箱")
    parser.add_argument("--password", help="GitHub 密码")
    parser.add_argument("--totp-secret", help="TOTP 密钥")
    parser.add_argument("--signin", action="store_true", help="仅登录（不注册）")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

    if not args.email:
        args.email = input("GitHub 邮箱: ").strip()
    if not args.password:
        args.password = getpass.getpass("GitHub 密码: ")
    if not args.totp_secret:
        args.totp_secret = getpass.getpass("TOTP 密钥: ")

    if not args.quiet:
        print("=" * 50)
        print(f"Devin {'登录' if args.signin else '注册'} — 通过 GitHub OAuth")
        print("=" * 50)

    t0 = time.time()
    result = devin_auth(
        args.email, args.password, args.totp_secret,
        signup=not args.signin, headless=args.headless, quiet=args.quiet,
    )

    if args.quiet:
        import json
        print(json.dumps(result))
    else:
        print(f"\n  耗时: {time.time()-t0:.1f}s")
        print("=" * 50)


if __name__ == "__main__":
    main()
