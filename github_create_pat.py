#!/usr/bin/env python3
"""
GitHub Fine-grained PAT 自动创建工具
====================================
用 Playwright 自动化浏览器操作，完成：
  1. 登录 GitHub（邮箱 + 密码 + TOTP 两步验证）
  2. 创建 Fine-grained Personal Access Token
  3. 输出生成的 Token 值

用法:
  python github_create_pat.py --email EMAIL --password PASSWORD --totp-secret TOTP_SECRET
  python github_create_pat.py  # 交互式输入

可选参数:
  --token-name NAME       Token 名称（默认: Auto-PAT-<timestamp>）
  --expiration DAYS       过期天数: 7/30/60/90/custom（默认: 365 即1年）
  --description DESC      Token 描述
  --repo-access ACCESS    仓库范围: all/public/selected（默认: all）
  --permissions PERMS     权限列表，逗号分隔（默认: administration,contents,issues,pull_requests,actions,deployments,metadata）
  --headless              无头模式运行（不显示浏览器窗口）
  --cdp URL               连接到已有的 Chrome DevTools Protocol 端点
"""

import argparse
import json
import sys
import time
import getpass
from datetime import datetime, timedelta

try:
    import pyotp
except ImportError:
    print("错误: 需要安装 pyotp 库。请运行: pip install pyotp")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("错误: 需要安装 playwright 库。请运行: pip install playwright && playwright install chromium")
    sys.exit(1)


# ── 默认权限配置 ────────────────────────────────────────────
# key: 权限搜索关键词, value: 访问级别 ("read" 或 "write")
DEFAULT_PERMISSIONS = {
    "Administration": "write",
    "Contents": "write",
    "Issues": "write",
    "Pull requests": "write",
    "Actions": "read",
    "Deployments": "read",
    "Metadata": "read",
}


def generate_totp_code(secret: str) -> str:
    """从 TOTP 密钥生成 6 位验证码"""
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    totp = pyotp.TOTP(clean)
    return totp.now()


def wait_and_fill(page, selector: str, value: str, timeout: int = 10000):
    """等待元素出现并填入内容"""
    el = page.wait_for_selector(selector, timeout=timeout)
    el.fill(value)
    return el


def wait_and_click(page, selector: str, timeout: int = 10000):
    """等待元素出现并点击"""
    el = page.wait_for_selector(selector, timeout=timeout)
    el.click()
    return el


def login_github(page, email: str, password: str, totp_secret: str):
    """登录 GitHub（邮箱 + 密码 + TOTP）"""
    print("[1/4] 正在登录 GitHub...")

    page.goto("https://github.com/login", wait_until="domcontentloaded")
    time.sleep(2)

    # 填写邮箱和密码
    wait_and_fill(page, "#login_field", email)
    wait_and_fill(page, "#password", password)
    wait_and_click(page, 'input[type="submit"], button[type="submit"]')

    # 等待页面跳转
    page.wait_for_load_state("domcontentloaded")
    time.sleep(3)

    # 检查是否需要 2FA
    current_url = page.url
    if "two-factor" in current_url or "sessions/two-factor" in current_url:
        print("  → 需要两步验证，正在生成 TOTP 验证码...")
        code = generate_totp_code(totp_secret)
        print(f"  → TOTP 验证码: {code}")

        # 查找 TOTP 输入框并填写
        totp_input = page.wait_for_selector(
            'input[name="app_otp"], input#app_totp, input[type="text"][autocomplete="one-time-code"]',
            timeout=10000,
        )
        totp_input.fill(code)

        # 有些情况下填完自动提交，有些需要手动点击
        time.sleep(3)
        if "two-factor" in page.url:
            # 尝试点击提交按钮
            try:
                page.click('button[type="submit"]', timeout=3000)
            except Exception:
                pass

        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)

    # 检查是否登录成功
    if "login" in page.url or "two-factor" in page.url:
        # 检查页面上是否有错误信息
        error_el = page.query_selector(".flash-error, .js-flash-alert")
        error_msg = error_el.inner_text() if error_el else "未知原因"
        raise RuntimeError(f"登录失败: {error_msg}")

    print("  → 登录成功！")


def navigate_to_pat_creation(page, context=None):
    """导航到 Fine-grained PAT 创建页面"""
    print("[2/4] 正在导航到 PAT 创建页面...")

    for attempt in range(3):
        try:
            if attempt > 0:
                print(f"  → 重试第 {attempt} 次...")
                if context:
                    page = context.new_page()
                time.sleep(2)

            page.goto(
                "https://github.com/settings/personal-access-tokens/new",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(3)

            # 检查是否需要 sudo 模式确认（密码或 2FA）
            if "confirm_access" in page.url or "sudo" in page.url:
                print("  → 需要 sudo 模式确认...")
                totp_field = page.query_selector(
                    'input[name="app_otp"], input#sudo_otp'
                )
                if totp_field:
                    code = generate_totp_code(args_global.totp_secret)
                    totp_field.fill(code)
                    page.click('button[type="submit"]')
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)

            # 确认到达了 PAT 创建页面
            if "personal-access-tokens" in page.url:
                print("  → 已到达 PAT 创建页面")
                return page

        except Exception as e:
            print(f"  → 导航失败: {e}")
            if attempt == 2:
                raise RuntimeError(f"多次尝试后仍无法到达 PAT 创建页面")

    raise RuntimeError(f"未能到达 PAT 创建页面，当前 URL: {page.url}")


def fill_pat_form(page, token_name: str, description: str, expiration_days: int,
                  repo_access: str, permissions: dict):
    """填写 PAT 创建表单"""
    print("[3/4] 正在配置 PAT...")

    # ── Token 名称 ──
    name_input = page.wait_for_selector(
        'input[name="user_programmatic_access[name]"]', timeout=15000
    )
    name_input.fill(token_name)
    print(f"  → Token 名称: {token_name}")
    time.sleep(1)

    # ── 描述 ──
    desc_textarea = page.query_selector(
        'textarea[name="user_programmatic_access[description]"]'
    )
    if desc_textarea:
        desc_textarea.fill(description)
    print(f"  → 描述: {description}")

    # ── 过期时间 ──
    _set_expiration(page, expiration_days)
    time.sleep(1)

    # ── 仓库范围 ──
    _set_repo_access(page, repo_access)
    time.sleep(1)

    # ── 权限配置 ──
    print("  → 正在配置权限...")
    _configure_permissions(page, permissions)

    print("  → PAT 配置完成！")


def _set_expiration(page, days: int):
    """设置过期时间"""
    # 找到 Expiration 下拉按钮 — 它的文本可能是 "30 days", "90 days", "Custom" 等
    expiry_btn = page.query_selector('action-menu button[type="button"]')
    if not expiry_btn:
        # 回退: 通过文本匹配
        for sel in ['button:has-text("days")', 'button:has-text("Custom")']:
            expiry_btn = page.query_selector(sel)
            if expiry_btn:
                break

    if not expiry_btn:
        print("  → 警告: 未找到过期时间选择器，使用默认值")
        return

    expiry_btn.click()
    time.sleep(1)

    # 标准选项
    standard_days = {7: "7 days", 30: "30 days", 60: "60 days", 90: "90 days"}

    if days in standard_days:
        option = page.query_selector(
            f'[role="menuitemradio"]:has-text("{standard_days[days]}")'
        )
        if option:
            option.click()
            print(f"  → 过期时间: {days} 天")
            return

    # 自定义天数 → 选择 Custom
    custom = page.query_selector('[role="menuitemradio"]:has-text("Custom")')
    if custom:
        custom.click()
        time.sleep(1)

    # 填写日期输入框
    target_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    date_input = page.query_selector('input[type="date"]')
    if date_input:
        date_input.fill(target_date)
        print(f"  → 过期时间: {days} 天 ({target_date})")
    else:
        print(f"  → 警告: 未找到日期输入框，使用默认过期时间")


def _set_repo_access(page, repo_access: str):
    """设置仓库访问范围"""
    # 使用 JavaScript 直接选中 radio button
    value_map = {"public": "none", "all": "all", "selected": "selected"}
    target_value = value_map.get(repo_access, "all")

    # 用 JS 点击对应的 radio
    clicked = page.evaluate(f"""() => {{
        const radios = document.querySelectorAll('input[name="install_target"]');
        for (const r of radios) {{
            if (r.value === '{target_value}' || r.getAttribute('text') === '{target_value}') {{
                r.click();
                return true;
            }}
        }}
        return false;
    }}""")

    if not clicked:
        # 回退: 通过 label 文本点击
        label_text = {
            "all": "All repositories",
            "public": "Public repositories",
            "selected": "Only select repositories",
        }.get(repo_access, "All repositories")
        label = page.query_selector(f'label:has-text("{label_text}")')
        if label:
            label.click()

    access_names = {"all": "所有仓库", "public": "仅公开仓库", "selected": "指定仓库"}
    print(f"  → 仓库范围: {access_names.get(repo_access, repo_access)}")


def _configure_permissions(page, permissions: dict):
    """配置权限"""
    for perm_name, access_level in permissions.items():
        if perm_name.lower() == "metadata":
            # Metadata 是必需且只读的，跳过
            print(f"    ✓ {perm_name}: Read-only (自动包含)")
            continue

        try:
            _add_single_permission(page, perm_name, access_level)
        except Exception as e:
            print(f"    ✗ {perm_name}: 配置失败 - {e}")


def _add_single_permission(page, perm_name: str, access_level: str):
    """添加单个权限"""
    # 点击 "Add permissions" 按钮打开权限选择面板
    add_btn = page.query_selector('button:has-text("Add permissions")')
    if add_btn:
        add_btn.click()
        time.sleep(1)

        # 在搜索框中搜索权限
        search_input = page.query_selector(
            'input[placeholder*="Filter"], input[placeholder*="Search"], input[type="text"][aria-label*="Filter"]'
        )
        if search_input:
            search_input.fill(perm_name)
            time.sleep(0.5)

        # 查找并勾选权限
        checkbox = page.query_selector(f'label:has-text("{perm_name}") input[type="checkbox"]')
        if not checkbox:
            # 尝试另一种选择器
            labels = page.query_selector_all("label")
            for label in labels:
                if perm_name.lower() in label.inner_text().lower():
                    cb = label.query_selector('input[type="checkbox"]')
                    if cb and not cb.is_checked():
                        cb.click()
                        checkbox = cb
                        break

        if checkbox and not checkbox.is_checked():
            checkbox.click()

        time.sleep(0.5)

        # 清空搜索框
        if search_input:
            search_input.fill("")
            time.sleep(0.3)

        # 关闭权限面板（点击外部区域）
        page.keyboard.press("Escape")
        time.sleep(0.5)

    # 如果需要 write 权限，修改访问级别
    if access_level == "write":
        _upgrade_permission_to_write(page, perm_name)

    level_text = "Read and write" if access_level == "write" else "Read-only"
    print(f"    ✓ {perm_name}: {level_text}")


def _upgrade_permission_to_write(page, perm_name: str):
    """将权限从 Read-only 升级为 Read and write"""
    # 找到对应权限行的下拉按钮
    perm_items = page.query_selector_all("li")
    for item in perm_items:
        label = item.get_attribute("aria-label") or ""
        if perm_name.lower() in label.lower():
            # 找到访问级别的下拉按钮
            dropdown = item.query_selector('button:has-text("Read-only")')
            if dropdown:
                dropdown.click()
                time.sleep(0.5)
                # 选择 "Read and write"
                rw_option = page.query_selector(
                    'li:has-text("Read and write"), [role="option"]:has-text("Read and write")'
                )
                if rw_option:
                    rw_option.click()
                    time.sleep(0.5)
            break


def generate_token(page) -> str:
    """点击生成按钮并获取 Token"""
    print("[4/4] 正在生成 Token...")

    # 点击 "Generate token" 按钮
    gen_btn = page.query_selector('button[type="submit"]:has-text("Generate token")')
    if gen_btn:
        gen_btn.click()
    else:
        raise RuntimeError("未找到 Generate token 按钮")

    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    # 检查是否出现确认对话框
    confirm_btn = page.query_selector(
        'dialog button[type="submit"]:has-text("Generate token")'
    )
    if confirm_btn:
        confirm_btn.click()
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)

    # 检查是否有错误
    error_banner = page.query_selector(".flash-error, .flash-warn")
    if error_banner:
        error_text = error_banner.inner_text()
        if "error" in error_text.lower():
            # 重试一次
            print("  → 遇到错误，正在重试...")
            time.sleep(2)
            gen_btn = page.query_selector(
                'button[type="submit"]:has-text("Generate token")'
            )
            if gen_btn:
                gen_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(2)
                confirm_btn = page.query_selector(
                    'dialog button[type="submit"]:has-text("Generate token")'
                )
                if confirm_btn:
                    confirm_btn.click()
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(3)

    # 提取 Token 值
    token_input = page.query_selector(
        'input[aria-label="Access token"], input[type="text"][value*="github_pat_"]'
    )
    if token_input:
        token_value = token_input.get_attribute("value") or token_input.input_value()
        if token_value and token_value.startswith("github_pat_"):
            print("  → Token 生成成功！")
            return token_value

    # 尝试从页面文本中提取
    page_text = page.content()
    import re
    match = re.search(r"(github_pat_[A-Za-z0-9_]+)", page_text)
    if match:
        token_value = match.group(1)
        print("  → Token 生成成功！")
        return token_value

    raise RuntimeError("未能获取生成的 Token 值")


def main():
    parser = argparse.ArgumentParser(
        description="GitHub Fine-grained PAT 自动创建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 命令行参数方式
  python github_create_pat.py --email user@example.com --password mypass --totp-secret JBSWY3DPEHPK3PXP

  # 交互式输入
  python github_create_pat.py

  # 自定义配置
  python github_create_pat.py --email user@example.com --password mypass --totp-secret SECRET \\
    --token-name "My-API-Token" --expiration 365 --headless
        """,
    )
    parser.add_argument("--email", help="GitHub 登录邮箱")
    parser.add_argument("--password", help="GitHub 登录密码")
    parser.add_argument("--totp-secret", help="TOTP 两步验证密钥（Base32 编码字符串）")
    parser.add_argument(
        "--token-name",
        default=None,
        help="Token 名称（默认: Auto-PAT-<timestamp>）",
    )
    parser.add_argument(
        "--expiration",
        type=int,
        default=365,
        help="过期天数（默认: 365，即1年）",
    )
    parser.add_argument(
        "--description",
        default="Auto-generated PAT for API access and automation",
        help="Token 描述",
    )
    parser.add_argument(
        "--repo-access",
        choices=["all", "public", "selected"],
        default="all",
        help="仓库访问范围（默认: all）",
    )
    parser.add_argument(
        "--permissions",
        default=None,
        help='权限配置 JSON 字符串，如: \'{"Administration":"write","Contents":"write"}\'',
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式（不显示浏览器窗口）",
    )
    parser.add_argument(
        "--cdp",
        default=None,
        help="连接到已有的 Chrome DevTools Protocol 端点",
    )

    args = parser.parse_args()

    # 全局保存 args 供 sudo 模式使用
    global args_global
    args_global = args

    # ── 交互式输入缺失的参数 ──
    if not args.email:
        args.email = input("请输入 GitHub 登录邮箱: ").strip()
    if not args.password:
        args.password = getpass.getpass("请输入 GitHub 登录密码: ")
    if not args.totp_secret:
        args.totp_secret = getpass.getpass("请输入 TOTP 密钥（Base32 字符串）: ")

    if not args.token_name:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.token_name = f"Auto-PAT-{ts}"

    # 解析权限配置
    if args.permissions:
        try:
            permissions = json.loads(args.permissions)
        except json.JSONDecodeError:
            print("错误: --permissions 必须是有效的 JSON 字符串")
            sys.exit(1)
    else:
        permissions = DEFAULT_PERMISSIONS.copy()

    # ── 打印配置摘要 ──
    print("=" * 60)
    print("GitHub Fine-grained PAT 自动创建工具")
    print("=" * 60)
    print(f"  邮箱:     {args.email}")
    print(f"  Token名:  {args.token_name}")
    print(f"  过期:     {args.expiration} 天")
    print(f"  仓库范围: {args.repo_access}")
    print(f"  权限数:   {len(permissions)} 项")
    for name, level in permissions.items():
        level_text = "Read and write" if level == "write" else "Read-only"
        print(f"    - {name}: {level_text}")
    print("=" * 60)
    print()

    # ── 启动浏览器 ──
    with sync_playwright() as pw:
        if args.cdp:
            print(f"正在连接到 CDP 端点: {args.cdp}")
            browser = pw.chromium.connect_over_cdp(args.cdp)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
        else:
            print(f"正在启动 Chromium（{'无头' if args.headless else '有头'}模式）...")
            browser = pw.chromium.launch(
                headless=args.headless,
                args=[
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

        try:
            # Step 1: 登录
            login_github(page, args.email, args.password, args.totp_secret)

            # Step 2: 导航到 PAT 创建页面
            page = navigate_to_pat_creation(page, context)

            # Step 3: 填写表单
            fill_pat_form(
                page,
                token_name=args.token_name,
                description=args.description,
                expiration_days=args.expiration,
                repo_access=args.repo_access,
                permissions=permissions,
            )

            # Step 4: 生成 Token
            token = generate_token(page)

            # ── 输出结果 ──
            print()
            print("=" * 60)
            print("PAT 创建成功！")
            print("=" * 60)
            print(f"  Token 名称:  {args.token_name}")
            print(f"  过期时间:    {args.expiration} 天后")
            print(f"  仓库范围:    {args.repo_access}")
            print(f"  权限数:      {len(permissions)} 项")
            print()
            print(f"  Token 值:")
            print(f"  {token}")
            print()
            print("⚠️  请立即保存此 Token，它不会再次显示！")
            print("=" * 60)

            # 输出纯 Token 到 stdout（方便管道使用）
            # 如果需要纯输出，可用: python script.py 2>/dev/null
            return token

        except Exception as e:
            print(f"\n❌ 错误: {e}")
            # 保存截图用于调试
            try:
                screenshot_path = f"/tmp/pat_error_{int(time.time())}.png"
                page.screenshot(path=screenshot_path)
                print(f"  错误截图已保存: {screenshot_path}")
            except Exception:
                pass
            sys.exit(1)

        finally:
            if not args.cdp:
                browser.close()


if __name__ == "__main__":
    token = main()
