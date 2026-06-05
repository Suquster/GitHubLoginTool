#!/usr/bin/env python3
"""
GitHub & Devin 自动化工具箱 — GUI 版
======================================
带图形界面的一键自动化工具，点击按钮即可：
  - 创建 GitHub Fine-grained PAT
  - 批量创建仓库 + 提交文件
  - 注册/登录 Devin（通过 GitHub OAuth）

弹出 Edge/Chrome 浏览器窗口，全程可视化操作。
自动识别系统上的浏览器路径。

依赖: pip install playwright pyotp requests
      playwright install chromium
"""

import base64
import json
import os
import platform
import random
import re
import string
import sys
import threading
import time
from datetime import datetime, timedelta

# ─── 依赖检查 ────────────────────────────────────────────────

try:
    import requests
except ImportError:
    requests = None

try:
    import pyotp
except ImportError:
    pyotp = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


# ─── 常量 ────────────────────────────────────────────────────

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


# ─── 浏览器路径自动检测 ──────────────────────────────────────

def detect_browser() -> tuple:
    """
    自动检测系统上的浏览器，优先 Edge，其次 Chrome/Chromium
    返回: (browser_type, executable_path) 或 (None, None)
    """
    system = platform.system()

    candidates = []

    if system == "Windows":
        prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")

        candidates = [
            ("msedge", os.path.join(prog, "Microsoft", "Edge", "Application", "msedge.exe")),
            ("msedge", os.path.join(prog86, "Microsoft", "Edge", "Application", "msedge.exe")),
            ("msedge", os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe")),
            ("chromium", os.path.join(prog, "Google", "Chrome", "Application", "chrome.exe")),
            ("chromium", os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe")),
            ("chromium", os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")),
        ]
    elif system == "Darwin":
        candidates = [
            ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:  # Linux
        candidates = [
            ("msedge", "/usr/bin/microsoft-edge"),
            ("msedge", "/usr/bin/microsoft-edge-stable"),
            ("msedge", "/opt/microsoft/msedge/msedge"),
            ("chromium", "/usr/bin/google-chrome"),
            ("chromium", "/usr/bin/google-chrome-stable"),
            ("chromium", "/usr/bin/chromium-browser"),
            ("chromium", "/usr/bin/chromium"),
        ]
        # Also check PATH
        import shutil
        for name in ["microsoft-edge", "microsoft-edge-stable"]:
            p = shutil.which(name)
            if p:
                candidates.insert(0, ("msedge", p))
        for name in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
            p = shutil.which(name)
            if p:
                candidates.append(("chromium", p))

    for btype, path in candidates:
        if os.path.isfile(path):
            return (btype, path)

    return (None, None)


# ─── 工具函数 ────────────────────────────────────────────────

def _totp_code(secret: str) -> str:
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    return pyotp.TOTP(clean).now()


def _totp_remaining(secret: str) -> int:
    clean = secret.replace(" ", "").replace("-", "").strip().upper()
    t = pyotp.TOTP(clean)
    return t.interval - int(time.time()) % t.interval


def _random_id(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }


# ─── 浏览器自动化核心 ────────────────────────────────────────

def _launch_browser(pw, browser_type: str, exe_path: str):
    """启动浏览器（有头模式，用户可观看）"""
    launcher = getattr(pw, browser_type, pw.chromium)
    return launcher.launch(
        headless=False,
        executable_path=exe_path if exe_path else None,
        args=["--start-maximized", "--disable-gpu", "--no-sandbox"],
    )


def _github_login_page(page, email, password, totp_secret, log):
    """处理 GitHub 登录页面"""
    log("  填写登录信息...")
    page.fill("#login_field", email)
    page.fill("#password", password)
    page.click('input[type="submit"]')
    time.sleep(4)
    page.wait_for_load_state("domcontentloaded")

    if "two-factor" in page.url:
        log("  输入两步验证码...")
        remaining = _totp_remaining(totp_secret)
        if remaining < 5:
            log(f"  等待 TOTP 刷新 ({remaining+2}s)...")
            time.sleep(remaining + 2)
        code = _totp_code(totp_secret)
        otp = page.query_selector('input[name="app_otp"]')
        if otp:
            otp.fill(code)
            time.sleep(4)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

    if "login" in page.url and "oauth" not in page.url and "two-factor" not in page.url:
        raise RuntimeError("GitHub 登录失败，请检查账号密码")
    log("  GitHub 登录成功 ✓")


def _github_authorize_page(page, totp_secret, log):
    """处理 GitHub OAuth 授权页面"""
    auth_btn = page.query_selector('button[name="authorize"], button#js-oauth-authorize-btn')
    if auth_btn:
        log("  点击授权按钮...")
        auth_btn.click()
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded")
        return

    sudo_otp = page.query_selector('input[name="sudo_otp"], input[name="otp"]')
    if sudo_otp:
        log("  需要验证身份...")
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
        auth_btn = page.query_selector('button[name="authorize"]')
        if auth_btn:
            auth_btn.click()
            time.sleep(5)


# ─── 功能实现 ────────────────────────────────────────────────

def action_create_pat(email, password, totp_secret, token_name, expiration,
                      browser_type, exe_path, log):
    """创建 Fine-grained PAT"""
    log("=" * 45)
    log("创建 Fine-grained PAT")
    log("=" * 45)
    t0 = time.time()

    with sync_playwright() as pw:
        browser = _launch_browser(pw, browser_type, exe_path)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=UA)
        page = ctx.new_page()

        try:
            log("[1/4] 登录 GitHub...")
            page.goto("https://github.com/login", wait_until="domcontentloaded")
            time.sleep(2)
            _github_login_page(page, email, password, totp_secret, log)

            log("[2/4] 打开 PAT 创建页面...")
            page.goto("https://github.com/settings/personal-access-tokens/new",
                       wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # Handle sudo mode
            if "confirm_access" in page.url or "sudo" in page.url:
                log("  需要验证身份...")
                remaining = _totp_remaining(totp_secret)
                if remaining < 5:
                    time.sleep(remaining + 2)
                code = _totp_code(totp_secret)
                otp_input = page.query_selector('input[name="sudo_otp"], input[name="app_otp"], #otp')
                if otp_input:
                    otp_input.fill(code)
                    submit = page.query_selector('button[type="submit"]')
                    if submit:
                        submit.click()
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(3)

            log("[3/4] 填写表单...")
            if not token_name:
                token_name = f"Auto-PAT-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            page.fill('input[name="user_programmatic_access[name]"]', token_name)
            time.sleep(0.5)

            # Expiration
            btn = page.query_selector("action-menu button[type='button']")
            if btn:
                standard = {7: "7 days", 30: "30 days", 60: "60 days", 90: "90 days"}
                if expiration in standard:
                    btn.click(); time.sleep(1)
                    opt = page.query_selector(f'[role="menuitemradio"]:has-text("{standard[expiration]}")')
                    if opt: opt.click(); time.sleep(0.5)
                else:
                    btn.click(); time.sleep(1)
                    custom = page.query_selector('[role="menuitemradio"]:has-text("Custom")')
                    if custom: custom.click(); time.sleep(1)
                    target = (datetime.now() + timedelta(days=expiration)).strftime("%Y-%m-%d")
                    date_input = page.query_selector('input[type="date"], input[name*="expires"]')
                    if date_input: date_input.fill(target); time.sleep(0.5)

            # Repo access: all
            page.evaluate("""() => {
                const radios = document.querySelectorAll('input[name="install_target"]');
                for (const r of radios) { if (r.value === 'all') { r.click(); break; } }
            }""")
            time.sleep(1)

            # Permissions
            for perm_name, level in DEFAULT_PAT_PERMISSIONS.items():
                if perm_name.lower() == "metadata":
                    continue
                try:
                    add_btn = page.query_selector('button:has-text("Add permissions")')
                    if add_btn:
                        add_btn.click(); time.sleep(1)
                        search = page.query_selector('input[placeholder*="Filter"], input[type="search"]')
                        if search: search.fill(perm_name); time.sleep(1)
                        for label in page.query_selector_all("label"):
                            if perm_name.lower() in label.inner_text().lower():
                                cb = label.query_selector('input[type="checkbox"]')
                                if cb and not cb.is_checked(): cb.click()
                                break
                        time.sleep(0.5)
                        if search: search.fill(""); time.sleep(0.3)
                        page.keyboard.press("Escape"); time.sleep(0.5)

                        if level == "write":
                            for item in page.query_selector_all("li"):
                                lbl = item.get_attribute("aria-label") or ""
                                if perm_name.lower() in lbl.lower():
                                    dd = item.query_selector('button:has-text("Read-only")')
                                    if dd:
                                        dd.click(); time.sleep(0.5)
                                        opt = page.query_selector('li:has-text("Read and write"), [role="option"]:has-text("Read and write")')
                                        if opt: opt.click(); time.sleep(0.5)
                                    break
                except Exception:
                    pass

            log("[4/4] 生成 Token...")
            gen_btn = page.query_selector('button[type="submit"]:has-text("Generate token")')
            if gen_btn: gen_btn.click()
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)

            confirm_btn = page.query_selector('dialog button[type="submit"]:has-text("Generate token")')
            if confirm_btn:
                confirm_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)

            token_match = re.search(r"github_pat_[A-Za-z0-9_]+", page.content())
            if token_match:
                token = token_match.group(0)
                elapsed = time.time() - t0
                log(f"\nToken: {token}")
                log(f"耗时: {elapsed:.1f}s")
                log("=" * 45)
                return token

            raise RuntimeError("未能提取 Token")

        finally:
            log("(浏览器窗口保留 10 秒供查看...)")
            time.sleep(10)
            browser.close()


def action_create_repos(token, pattern, count, description, public,
                        license_tpl, delay, init_content, log):
    """用 API 批量创建仓库"""
    log("=" * 45)
    log("创建仓库")
    log("=" * 45)
    t0 = time.time()

    headers = _api_headers(token)
    r = requests.get(f"{API_BASE}/user", headers=headers)
    r.raise_for_status()
    owner = r.json()["login"]
    log(f"用户: {owner}")

    repos = []
    for i in range(1, count + 1):
        name = pattern
        if "{i}" in name:
            name = name.replace("{i}", str(i))
        elif "{rand}" in name:
            name = name.replace("{rand}", _random_id())
        elif count > 1:
            name = f"{name}-{i}"

        data = {"name": name, "description": description,
                "private": not public, "auto_init": True}
        if license_tpl:
            data["license_template"] = license_tpl

        r = requests.post(f"{API_BASE}/user/repos", headers=headers, json=data)
        if r.status_code in (200, 201):
            info = r.json()
            vis = "public" if public else "private"
            log(f"  [{i}/{count}] {info['full_name']} ({vis})")
            repos.append(info)
        else:
            log(f"  [{i}/{count}] 失败: {r.status_code} {r.text[:80]}")

        if i < count and delay > 0:
            time.sleep(delay)

    # Init content
    if init_content and repos:
        log(f"\n初始化 README...")
        for info in repos:
            readme = f"# {info['name']}\n\n{init_content}\n"
            encoded = base64.b64encode(readme.encode()).decode()
            api_path = f"/repos/{owner}/{info['name']}/contents/README.md"
            # Get existing SHA
            sha = None
            try:
                existing = requests.get(f"{API_BASE}{api_path}?ref=main", headers=headers)
                if existing.status_code == 200:
                    sha = existing.json()["sha"]
            except Exception:
                pass
            put_data = {"message": "Initialize README.md", "content": encoded, "branch": "main"}
            if sha:
                put_data["sha"] = sha
            r = requests.put(f"{API_BASE}{api_path}", headers=headers, json=put_data)
            if r.status_code in (200, 201):
                log(f"  已初始化: {info['name']}/README.md")

    elapsed = time.time() - t0
    log(f"\n创建了 {len(repos)} 个仓库，耗时 {elapsed:.1f}s")
    log("=" * 45)
    return repos


def action_devin_auth(email, password, totp_secret, signup,
                      browser_type, exe_path, log):
    """通过 GitHub OAuth 注册/登录 Devin"""
    mode_cn = "注册" if signup else "登录"
    log("=" * 45)
    log(f"Devin {mode_cn}（通过 GitHub OAuth）")
    log("=" * 45)
    t0 = time.time()

    start_url = "https://app.devin.ai/signup" if signup else "https://app.devin.ai/auth/login"

    with sync_playwright() as pw:
        browser = _launch_browser(pw, browser_type, exe_path)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=UA)
        page = ctx.new_page()

        try:
            log(f"[1/4] 打开 Devin {mode_cn}页面...")
            page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "/org/" in page.url and "auth" not in page.url:
                log("  已经登录，直接进入控制台！")
                elapsed = time.time() - t0
                log(f"\n{mode_cn}完成！耗时 {elapsed:.1f}s")
                log(f"URL: {page.url}")
                log("=" * 45)
                time.sleep(10)
                return page.url

            log("[2/4] 点击 'Continue with GitHub'...")
            gh_btn = page.query_selector('button:has-text("Continue with GitHub")')
            if not gh_btn:
                raise RuntimeError("找不到 GitHub 按钮")
            gh_btn.click()
            time.sleep(5)
            page.wait_for_load_state("domcontentloaded")

            if "github.com/login" in page.url:
                log("[3/4] 登录 GitHub...")
                _github_login_page(page, email, password, totp_secret, log)
                time.sleep(5)
                page.wait_for_load_state("domcontentloaded")

            if "github.com/login/oauth/authorize" in page.url:
                log("[3/4] 授权 Devin...")
                _github_authorize_page(page, totp_secret, log)
                time.sleep(5)
                page.wait_for_load_state("domcontentloaded")

            log("[4/4] 处理后续...")
            for _ in range(30):
                if "app.devin.ai" in page.url:
                    break
                time.sleep(1)
            time.sleep(3)

            if "/auth/upgrade" in page.url:
                log("  选择免费版...")
                free_btn = page.query_selector('a:has-text("Continue with free")')
                if free_btn:
                    free_btn.click()
                    time.sleep(3)

            elapsed = time.time() - t0
            log(f"\n{mode_cn}完成！耗时 {elapsed:.1f}s")
            log(f"URL: {page.url}")
            log("=" * 45)

            log("(浏览器窗口保留 10 秒供查看...)")
            time.sleep(10)
            return page.url

        finally:
            browser.close()


# ─── GUI ─────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GitHub & Devin 自动化工具箱")
        self.geometry("720x680")
        self.resizable(True, True)

        # Detect browser
        self.browser_type, self.browser_path = detect_browser()

        self._build_ui()
        self._check_deps()

    def _build_ui(self):
        # ─── 顶部：凭据输入 ─────────────
        cred_frame = ttk.LabelFrame(self, text="GitHub 账号", padding=10)
        cred_frame.pack(fill="x", padx=10, pady=(10, 5))

        row = ttk.Frame(cred_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="邮箱:", width=10).pack(side="left")
        self.email_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.email_var, width=45).pack(side="left", fill="x", expand=True)

        row = ttk.Frame(cred_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="密码:", width=10).pack(side="left")
        self.password_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.password_var, show="*", width=45).pack(side="left", fill="x", expand=True)

        row = ttk.Frame(cred_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="TOTP 密钥:", width=10).pack(side="left")
        self.totp_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.totp_var, show="*", width=45).pack(side="left", fill="x", expand=True)

        # ─── 浏览器信息 ─────────────
        browser_frame = ttk.LabelFrame(self, text="浏览器", padding=5)
        browser_frame.pack(fill="x", padx=10, pady=5)

        if self.browser_path:
            bname = "Edge" if "edge" in (self.browser_path or "").lower() else "Chrome"
            info = f"{bname} — {self.browser_path}"
        else:
            info = "未检测到 Edge/Chrome，将使用 Playwright 内置 Chromium"
        self.browser_label = ttk.Label(browser_frame, text=info, foreground="gray")
        self.browser_label.pack(anchor="w")

        # ─── 功能按钮 ─────────────
        btn_frame = ttk.LabelFrame(self, text="功能", padding=10)
        btn_frame.pack(fill="x", padx=10, pady=5)

        row1 = ttk.Frame(btn_frame)
        row1.pack(fill="x", pady=3)
        self.btn_pat = ttk.Button(row1, text="创建 PAT", command=self._on_create_pat)
        self.btn_pat.pack(side="left", padx=3, expand=True, fill="x")
        self.btn_devin_signup = ttk.Button(row1, text="注册 Devin", command=self._on_devin_signup)
        self.btn_devin_signup.pack(side="left", padx=3, expand=True, fill="x")
        self.btn_devin_login = ttk.Button(row1, text="登录 Devin", command=self._on_devin_login)
        self.btn_devin_login.pack(side="left", padx=3, expand=True, fill="x")

        row2 = ttk.Frame(btn_frame)
        row2.pack(fill="x", pady=3)

        ttk.Label(row2, text="PAT:").pack(side="left")
        self.token_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.token_var, width=35).pack(side="left", padx=3)

        self.btn_repos = ttk.Button(row2, text="创建仓库", command=self._on_create_repos)
        self.btn_repos.pack(side="left", padx=3)

        # ─── 仓库选项 ─────────────
        repo_frame = ttk.LabelFrame(self, text="仓库选项", padding=5)
        repo_frame.pack(fill="x", padx=10, pady=5)

        row = ttk.Frame(repo_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="名称模式:", width=10).pack(side="left")
        self.repo_name_var = tk.StringVar(value="repo-{rand}")
        ttk.Entry(row, textvariable=self.repo_name_var, width=25).pack(side="left")
        ttk.Label(row, text="  数量:").pack(side="left")
        self.repo_count_var = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=100, textvariable=self.repo_count_var, width=5).pack(side="left")
        self.public_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="公开", variable=self.public_var).pack(side="left", padx=10)

        row = ttk.Frame(repo_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="描述:", width=10).pack(side="left")
        self.repo_desc_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.repo_desc_var, width=45).pack(side="left", fill="x", expand=True)

        row = ttk.Frame(repo_frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="初始内容:", width=10).pack(side="left")
        self.init_content_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.init_content_var, width=45).pack(side="left", fill="x", expand=True)

        # ─── 日志 ─────────────
        log_frame = ttk.LabelFrame(self, text="日志", padding=5)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)

    def _check_deps(self):
        missing = []
        if not requests:
            missing.append("requests")
        if not pyotp:
            missing.append("pyotp")
        if not sync_playwright:
            missing.append("playwright")
        if missing:
            self.log(f"缺少依赖: pip install {' '.join(missing)}")
            if not sync_playwright:
                self.log("还需要: playwright install chromium")

    def log(self, msg: str):
        """线程安全的日志输出"""
        def _write():
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self.after(0, _write)

    def _validate_creds(self):
        if not self.email_var.get().strip():
            messagebox.showwarning("提示", "请输入 GitHub 邮箱")
            return False
        if not self.password_var.get():
            messagebox.showwarning("提示", "请输入 GitHub 密码")
            return False
        if not self.totp_var.get():
            messagebox.showwarning("提示", "请输入 TOTP 密钥")
            return False
        return True

    def _set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in [self.btn_pat, self.btn_devin_signup, self.btn_devin_login, self.btn_repos]:
            btn.configure(state=state)

    def _run_in_thread(self, func):
        """在后台线程运行，避免 GUI 卡死"""
        self._set_buttons(False)

        def wrapper():
            try:
                func()
            except Exception as e:
                self.log(f"\n错误: {e}")
            finally:
                self.after(0, lambda: self._set_buttons(True))

        t = threading.Thread(target=wrapper, daemon=True)
        t.start()

    # ─── 按钮回调 ─────────────

    def _on_create_pat(self):
        if not self._validate_creds():
            return

        def run():
            token = action_create_pat(
                self.email_var.get().strip(),
                self.password_var.get(),
                self.totp_var.get().strip(),
                token_name="",
                expiration=365,
                browser_type=self.browser_type or "chromium",
                exe_path=self.browser_path,
                log=self.log,
            )
            if token:
                self.after(0, lambda: self.token_var.set(token))
                self.log("\nToken 已自动填入 PAT 输入框，可直接用于创建仓库")

        self._run_in_thread(run)

    def _on_devin_signup(self):
        if not self._validate_creds():
            return

        def run():
            action_devin_auth(
                self.email_var.get().strip(),
                self.password_var.get(),
                self.totp_var.get().strip(),
                signup=True,
                browser_type=self.browser_type or "chromium",
                exe_path=self.browser_path,
                log=self.log,
            )

        self._run_in_thread(run)

    def _on_devin_login(self):
        if not self._validate_creds():
            return

        def run():
            action_devin_auth(
                self.email_var.get().strip(),
                self.password_var.get(),
                self.totp_var.get().strip(),
                signup=False,
                browser_type=self.browser_type or "chromium",
                exe_path=self.browser_path,
                log=self.log,
            )

        self._run_in_thread(run)

    def _on_create_repos(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("提示", "请先输入或创建 PAT")
            return

        def run():
            action_create_repos(
                token=token,
                pattern=self.repo_name_var.get().strip() or "repo-{rand}",
                count=self.repo_count_var.get(),
                description=self.repo_desc_var.get().strip(),
                public=self.public_var.get(),
                license_tpl="mit",
                delay=1.0,
                init_content=self.init_content_var.get().strip(),
                log=self.log,
            )

        self._run_in_thread(run)


# ─── 主入口 ──────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
