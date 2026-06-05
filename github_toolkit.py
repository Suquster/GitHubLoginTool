#!/usr/bin/env python3
"""
GitHub 自动化工具箱
==================
一个脚本搞定：创建 PAT → 创建仓库 → 提交文件 → 批量操作

依赖: pip install requests pyotp playwright
      playwright install chromium

子命令:
  pat       创建 Fine-grained PAT（通过浏览器自动化，约 30 秒）
  repo      创建仓库（支持批量，使用 API，秒级完成）
  commit    向仓库提交文件（使用 API）
  list      列出所有仓库
  delete    删除仓库
  pipeline  一键流水线：创建 PAT + 批量创建仓库 + 初始化内容

示例:
  # 创建 PAT（需要邮箱/密码/TOTP）
  python github_toolkit.py pat --email x --password x --totp-secret x

  # 用已有 PAT 创建仓库
  python github_toolkit.py repo --token github_pat_xxx --name my-repo --public

  # 批量创建 5 个仓库
  python github_toolkit.py repo --token github_pat_xxx --name "proj-{i}" --count 5 --public

  # 提交文件
  python github_toolkit.py commit --token github_pat_xxx --repo my-repo --file src/main.py --content "print('hello')"

  # 一键流水线
  python github_toolkit.py pipeline --email x --password x --totp-secret x --count 3
"""

import argparse
import base64
import getpass
import json
import random
import re
import string
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


# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

API_BASE = "https://api.github.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

DEFAULT_PAT_PERMISSIONS = {
    "Administration": "write",
    "Contents": "write",
    "Issues": "write",
    "Pull requests": "write",
    "Actions": "read",
    "Deployments": "read",
    "Metadata": "read",
}


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _totp_code(secret: str) -> str:
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).now()


def _random_id(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }


def _api_get(token: str, path: str) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_api_headers(token))
    r.raise_for_status()
    return r.json()


def _api_post(token: str, path: str, data: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=_api_headers(token), json=data)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"API 错误 {r.status_code}: {r.text[:200]}")
    return r.json()


def _api_put(token: str, path: str, data: dict) -> dict:
    r = requests.put(f"{API_BASE}{path}", headers=_api_headers(token), json=data)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"API 错误 {r.status_code}: {r.text[:200]}")
    return r.json()


# ─────────────────────────────────────────────────────────────
# PAT 创建（Playwright 浏览器自动化）
# ─────────────────────────────────────────────────────────────

def create_pat(email: str, password: str, totp_secret: str,
               token_name: str, description: str, expiration_days: int,
               repo_access: str, permissions: dict,
               quiet: bool = False) -> str:
    """创建 Fine-grained PAT，返回 token 字符串"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("错误: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    if not quiet:
        print("[1/4] 启动浏览器...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox",
                  "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=UA,
        )
        page = ctx.new_page()

        try:
            # Login
            if not quiet:
                print("[2/4] 登录 GitHub...")
            page.goto("https://github.com/login", wait_until="domcontentloaded")
            time.sleep(2)
            page.fill("#login_field", email)
            page.fill("#password", password)
            page.click('input[type="submit"]')
            page.wait_for_load_state("domcontentloaded")
            time.sleep(3)

            if "two-factor" in page.url:
                if not quiet:
                    print("  → 两步验证...")
                code = _totp_code(totp_secret)
                page.fill('input[name="app_otp"]', code)
                time.sleep(4)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(2)

            if "login" in page.url or "two-factor" in page.url:
                raise RuntimeError("登录失败")

            if not quiet:
                print("  → 登录成功")

            # Navigate to PAT form
            if not quiet:
                print("[3/4] 填写 PAT 表单...")

            for attempt in range(3):
                try:
                    page.goto("https://github.com/settings/personal-access-tokens/new",
                              wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2)

            # Handle sudo mode
            if "confirm_access" in page.url or "sudo" in page.url:
                code = _totp_code(totp_secret)
                otp_input = page.query_selector('input[name="sudo_otp"], input[name="app_otp"], #otp')
                if otp_input:
                    otp_input.fill(code)
                    submit = page.query_selector('button[type="submit"]')
                    if submit:
                        submit.click()
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(3)

            # Fill token name
            page.fill('input[name="user_programmatic_access[name]"]', token_name)
            time.sleep(0.5)

            # Set expiration
            _set_expiration_browser(page, expiration_days)

            # Set repo access
            _set_repo_access_browser(page, repo_access)
            time.sleep(1)

            # Set permissions
            _configure_permissions_browser(page, permissions, quiet)
            time.sleep(1)

            # Generate token
            if not quiet:
                print("[4/4] 生成 Token...")
            gen_btn = page.query_selector('button[type="submit"]:has-text("Generate token")')
            if gen_btn:
                gen_btn.click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

            # Handle confirmation dialog
            confirm_btn = page.query_selector('dialog button[type="submit"]:has-text("Generate token")')
            if confirm_btn:
                confirm_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)

            # Extract token
            token_match = re.search(r"github_pat_[A-Za-z0-9_]+", page.content())
            if token_match:
                token = token_match.group(0)
                if not quiet:
                    print(f"  → Token: {token[:25]}...{token[-10:]}")
                return token

            raise RuntimeError("未能提取 Token 值")

        finally:
            browser.close()


def _set_expiration_browser(page, days: int):
    """设置过期时间"""
    btn = page.query_selector("action-menu button[type='button']")
    if not btn:
        return

    standard_map = {7: "7 days", 30: "30 days", 60: "60 days", 90: "90 days"}
    if days in standard_map:
        btn.click()
        time.sleep(1)
        opt = page.query_selector(f'[role="menuitemradio"]:has-text("{standard_map[days]}")')
        if opt:
            opt.click()
            time.sleep(0.5)
    else:
        btn.click()
        time.sleep(1)
        custom = page.query_selector('[role="menuitemradio"]:has-text("Custom")')
        if custom:
            custom.click()
            time.sleep(1)
        target = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        date_input = page.query_selector('input[type="date"], input[name*="expires"]')
        if date_input:
            date_input.fill(target)
            time.sleep(0.5)


def _set_repo_access_browser(page, access: str):
    """设置仓库访问范围"""
    value_map = {"public": "none", "all": "all", "selected": "selected"}
    val = value_map.get(access, "all")
    page.evaluate(f"""() => {{
        const radios = document.querySelectorAll('input[name="install_target"]');
        for (const r of radios) {{ if (r.value === '{val}') {{ r.click(); break; }} }}
    }}""")
    time.sleep(1)


def _configure_permissions_browser(page, permissions: dict, quiet: bool):
    """配置权限"""
    for perm_name, access_level in permissions.items():
        if perm_name.lower() == "metadata":
            continue
        try:
            _add_permission_browser(page, perm_name, access_level)
        except Exception as e:
            if not quiet:
                print(f"  ⚠ 权限 {perm_name} 配置失败: {e}")


def _add_permission_browser(page, perm_name: str, access_level: str):
    """添加单个权限"""
    add_btn = page.query_selector('button:has-text("Add permissions")')
    if not add_btn:
        return

    add_btn.click()
    time.sleep(1)

    search = page.query_selector('input[placeholder*="Filter"], input[type="search"]')
    if search:
        search.fill(perm_name)
        time.sleep(1)

    # Find and click checkbox
    checkbox = None
    labels = page.query_selector_all("label")
    for label in labels:
        if perm_name.lower() in label.inner_text().lower():
            cb = label.query_selector('input[type="checkbox"]')
            if cb and not cb.is_checked():
                cb.click()
                checkbox = cb
                break

    time.sleep(0.5)
    if search:
        search.fill("")
        time.sleep(0.3)
    page.keyboard.press("Escape")
    time.sleep(0.5)

    if access_level == "write":
        _upgrade_to_write_browser(page, perm_name)


def _upgrade_to_write_browser(page, perm_name: str):
    """升级权限为 Read and write"""
    items = page.query_selector_all("li")
    for item in items:
        label = item.get_attribute("aria-label") or ""
        if perm_name.lower() in label.lower():
            dd = item.query_selector('button:has-text("Read-only")')
            if dd:
                dd.click()
                time.sleep(0.5)
                opt = page.query_selector('li:has-text("Read and write"), [role="option"]:has-text("Read and write")')
                if opt:
                    opt.click()
                    time.sleep(0.5)
            break


# ─────────────────────────────────────────────────────────────
# 仓库操作（GitHub REST API — 极速）
# ─────────────────────────────────────────────────────────────

def get_user(token: str) -> str:
    """获取当前用户名"""
    return _api_get(token, "/user")["login"]


def create_repo(token: str, name: str, description: str = "",
                private: bool = False, auto_init: bool = True,
                license_template: str = "mit", quiet: bool = False) -> dict:
    """创建仓库"""
    data = {"name": name, "description": description,
            "private": private, "auto_init": auto_init}
    if license_template:
        data["license_template"] = license_template

    result = _api_post(token, "/user/repos", data)
    if not quiet:
        vis = "private" if result.get("private") else "public"
        print(f"  → {result['full_name']} ({vis}) — {result['html_url']}")

    return {"name": result["name"], "full_name": result["full_name"],
            "html_url": result["html_url"], "clone_url": result["clone_url"]}


def commit_file(token: str, owner: str, repo: str, path: str,
                content: str, message: str = None,
                branch: str = "main", quiet: bool = False) -> dict:
    """提交/更新文件"""
    if not message:
        message = f"Add {path}"

    encoded = base64.b64encode(content.encode()).decode()
    api_path = f"/repos/{owner}/{repo}/contents/{path}"
    data = {"message": message, "content": encoded, "branch": branch}

    try:
        existing = _api_get(token, f"{api_path}?ref={branch}")
        data["sha"] = existing["sha"]
    except Exception:
        pass

    result = _api_put(token, api_path, data)
    if not quiet:
        print(f"  → 已提交: {path}")
    return {"path": result["content"]["path"], "sha": result["content"]["sha"]}


def batch_create_repos(token: str, pattern: str, count: int,
                       description: str = "", private: bool = False,
                       auto_init: bool = True, license_template: str = "mit",
                       delay: float = 1.0, quiet: bool = False) -> list:
    """批量创建仓库"""
    repos = []
    if not quiet:
        print(f"[批量] 创建 {count} 个仓库...")

    for i in range(1, count + 1):
        name = pattern
        if "{i}" in name:
            name = name.replace("{i}", str(i))
        elif "{rand}" in name:
            name = name.replace("{rand}", _random_id())
        elif count > 1:
            name = f"{name}-{i}"

        try:
            repo = create_repo(token, name, description=description,
                               private=private, auto_init=auto_init,
                               license_template=license_template, quiet=quiet)
            repos.append(repo)
        except Exception as e:
            if not quiet:
                print(f"  ✗ {name}: {e}")

        if i < count and delay > 0:
            time.sleep(delay)

    return repos


def list_repos(token: str, quiet: bool = False) -> list:
    """列出所有仓库"""
    repos, page = [], 1
    while True:
        r = requests.get(f"{API_BASE}/user/repos?per_page=100&page={page}&sort=created&direction=desc",
                         headers=_api_headers(token))
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1

    if not quiet:
        print(f"共 {len(repos)} 个仓库:")
        for r in repos:
            vis = "private" if r["private"] else "public"
            print(f"  [{vis}] {r['full_name']} — {r.get('description') or ''}")
    return repos


def delete_repo(token: str, owner: str, repo: str, quiet: bool = False) -> bool:
    """删除仓库"""
    r = requests.delete(f"{API_BASE}/repos/{owner}/{repo}", headers=_api_headers(token))
    if r.status_code == 204:
        if not quiet:
            print(f"  → 已删除: {owner}/{repo}")
        return True
    if not quiet:
        print(f"  ✗ 删除失败: {r.status_code}")
    return False


# ─────────────────────────────────────────────────────────────
# CLI 子命令
# ─────────────────────────────────────────────────────────────

def cmd_pat(args):
    if not args.email:
        args.email = input("GitHub 邮箱: ").strip()
    if not args.password:
        args.password = getpass.getpass("GitHub 密码: ")
    if not args.totp_secret:
        args.totp_secret = getpass.getpass("TOTP 密钥: ")
    if not args.token_name:
        args.token_name = f"Auto-PAT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    permissions = json.loads(args.permissions) if args.permissions else DEFAULT_PAT_PERMISSIONS.copy()

    if not args.quiet:
        print("=" * 50)
        print("创建 Fine-grained PAT")
        print("=" * 50)

    t0 = time.time()
    token = create_pat(
        args.email, args.password, args.totp_secret,
        args.token_name, args.description, args.expiration,
        args.repo_access, permissions, args.quiet,
    )

    if args.quiet:
        print(token)
    else:
        print(f"\n  Token: {token}")
        print(f"  耗时: {time.time()-t0:.1f}s")
        print("=" * 50)


def cmd_repo(args):
    if not args.token:
        args.token = getpass.getpass("GitHub PAT: ")

    owner = get_user(args.token)
    private = not args.public

    if not args.quiet:
        print("=" * 50)
        print(f"创建仓库 (用户: {owner})")
        print("=" * 50)

    t0 = time.time()
    if args.count > 1:
        repos = batch_create_repos(
            args.token, args.name, args.count,
            description=args.description, private=private,
            auto_init=True, license_template=args.license or "",
            delay=args.delay, quiet=args.quiet,
        )
    else:
        repos = [create_repo(
            args.token, args.name, description=args.description,
            private=private, auto_init=True,
            license_template=args.license or "", quiet=args.quiet,
        )]

    if not args.quiet:
        print(f"\n  创建了 {len(repos)} 个仓库，耗时 {time.time()-t0:.1f}s")
        print("=" * 50)


def cmd_commit(args):
    if not args.token:
        args.token = getpass.getpass("GitHub PAT: ")

    owner = get_user(args.token)

    if args.content:
        content = args.content
    elif args.content_file:
        with open(args.content_file) as f:
            content = f.read()
    else:
        content = f"# {args.repo}\n"

    if not args.quiet:
        print(f"提交到 {owner}/{args.repo}...")

    commit_file(args.token, owner, args.repo, args.file,
                content, message=args.message, branch=args.branch, quiet=args.quiet)


def cmd_list(args):
    if not args.token:
        args.token = getpass.getpass("GitHub PAT: ")
    list_repos(args.token, args.quiet)


def cmd_delete(args):
    if not args.token:
        args.token = getpass.getpass("GitHub PAT: ")

    owner = get_user(args.token)
    if not args.yes:
        confirm = input(f"确认删除 {owner}/{args.name}? (y/N): ")
        if confirm.lower() != "y":
            print("已取消")
            return
    delete_repo(args.token, owner, args.name, args.quiet)


def cmd_pipeline(args):
    """一键流水线: PAT → 仓库 → 初始化"""
    if not args.email:
        args.email = input("GitHub 邮箱: ").strip()
    if not args.password:
        args.password = getpass.getpass("GitHub 密码: ")
    if not args.totp_secret:
        args.totp_secret = getpass.getpass("TOTP 密钥: ")

    quiet = args.quiet
    count = args.count
    pattern = args.name or "repo-{rand}"
    private = not args.public

    if not quiet:
        print("=" * 50)
        print("一键流水线: PAT → 仓库 → 初始化")
        print("=" * 50)

    t0 = time.time()

    # Step 1: PAT
    pat_name = args.token_name or f"Pipeline-PAT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    permissions = DEFAULT_PAT_PERMISSIONS.copy()

    token = create_pat(
        args.email, args.password, args.totp_secret,
        pat_name, "Pipeline auto-generated PAT",
        args.expiration, "all", permissions, quiet,
    )

    # Step 2: 创建仓库
    owner = get_user(token)
    if not quiet:
        print(f"\n[仓库] 用户 {owner}，创建 {count} 个仓库...")

    repos = batch_create_repos(
        token, pattern, count,
        description=args.description or "",
        private=private, auto_init=True,
        license_template=args.license or "mit",
        delay=args.delay, quiet=quiet,
    )

    # Step 3: 初始化内容
    if args.init_content:
        if not quiet:
            print(f"\n[初始化] 写入 README...")
        for repo in repos:
            readme = f"# {repo['name']}\n\n{args.init_content}\n"
            commit_file(token, owner, repo["name"], "README.md", readme,
                        message="Initialize README.md", quiet=quiet)

    elapsed = time.time() - t0

    if quiet:
        print(json.dumps({"token": token, "repos": [r["full_name"] for r in repos]}))
    else:
        print(f"\n{'='*50}")
        print("流水线完成！")
        print(f"{'='*50}")
        print(f"  PAT: {token}")
        print(f"  仓库:")
        for r in repos:
            print(f"    → {r['html_url']}")
        print(f"  总耗时: {elapsed:.1f}s")
        print(f"{'='*50}")


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GitHub 自动化工具箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # pat
    p = sub.add_parser("pat", help="创建 Fine-grained PAT")
    p.add_argument("--email"); p.add_argument("--password"); p.add_argument("--totp-secret")
    p.add_argument("--token-name"); p.add_argument("--expiration", type=int, default=365)
    p.add_argument("--description", default="Auto-generated PAT")
    p.add_argument("--repo-access", choices=["all", "public"], default="all")
    p.add_argument("--permissions"); p.add_argument("-q", "--quiet", action="store_true")

    # repo
    p = sub.add_parser("repo", help="创建仓库（支持批量）")
    p.add_argument("--token"); p.add_argument("--name", required=True)
    p.add_argument("--count", type=int, default=1); p.add_argument("--description", default="")
    p.add_argument("--public", action="store_true"); p.add_argument("--license", default="mit")
    p.add_argument("--delay", type=float, default=1.0); p.add_argument("-q", "--quiet", action="store_true")

    # commit
    p = sub.add_parser("commit", help="提交文件到仓库")
    p.add_argument("--token"); p.add_argument("--repo", required=True)
    p.add_argument("--file", required=True); p.add_argument("--content")
    p.add_argument("--content-file"); p.add_argument("--message")
    p.add_argument("--branch", default="main"); p.add_argument("-q", "--quiet", action="store_true")

    # list
    p = sub.add_parser("list", help="列出所有仓库")
    p.add_argument("--token"); p.add_argument("-q", "--quiet", action="store_true")

    # delete
    p = sub.add_parser("delete", help="删除仓库")
    p.add_argument("--token"); p.add_argument("--name", required=True)
    p.add_argument("-y", "--yes", action="store_true"); p.add_argument("-q", "--quiet", action="store_true")

    # pipeline
    p = sub.add_parser("pipeline", help="一键流水线: PAT → 仓库 → 初始化")
    p.add_argument("--email"); p.add_argument("--password"); p.add_argument("--totp-secret")
    p.add_argument("--token-name"); p.add_argument("--expiration", type=int, default=365)
    p.add_argument("--name"); p.add_argument("--count", type=int, default=1)
    p.add_argument("--description", default=""); p.add_argument("--public", action="store_true")
    p.add_argument("--license", default="mit"); p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--init-content"); p.add_argument("-q", "--quiet", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmds = {"pat": cmd_pat, "repo": cmd_repo, "commit": cmd_commit,
            "list": cmd_list, "delete": cmd_delete, "pipeline": cmd_pipeline}

    try:
        cmds[args.command](args)
    except RuntimeError as e:
        print(f"\n错误: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(130)


if __name__ == "__main__":
    main()
