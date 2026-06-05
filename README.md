# GitHubLoginTool

GitHub 自动化工具箱 — 登录、2FA 设置、创建 PAT、批量创建仓库、注册 Devin，一键搞定。

## 功能一览

| 功能 | 工具 | 方式 | 速度 |
|------|------|------|------|
| GitHub 登录 | `github_auto_login.py` | HTTP (requests) | ~2s |
| GitHub 登录 (Web GUI) | `github_login_web.py` | Flask 网页界面 | ~2s |
| 自动开启 2FA | `github_2fa_setup.py` | Playwright CDP | ~30s |
| 创建 PAT (Playwright) | `github_create_pat.py` | 浏览器自动化 | ~30s |
| 创建 PAT (HTTP 极速) | `github_create_pat_fast.py` | 纯 HTTP 请求 | ~3s |
| 完整工具箱 | `github_toolkit.py` | PAT + 仓库 + 提交 (CLI) | 按操作 |
| Devin 注册/登录 | `devin_signup.py` | GitHub OAuth 自动化 | ~35s |
| **桌面 GUI 版** | `github_gui.py` | **tkinter 图形界面** | 一键操作 |

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 方式一：桌面 GUI（推荐）

```bash
python github_gui.py
```

弹出图形界面 → 填入邮箱/密码/TOTP 密钥 → 点击按钮即可。
会自动弹出 Edge/Chrome 浏览器窗口，全程可视化操作。

**自动检测浏览器路径**：优先 Edge，其次 Chrome，最后 Playwright 内置 Chromium。

### 方式二：Flask 网页 GUI

**Windows**: 双击 `启动.bat`

**Linux/Mac**: 运行 `bash 启动.sh`

然后在浏览器中输入账号信息，格式：`账号----密码----TOTP密钥`

### 方式三：命令行

```bash
# 登录 GitHub
python github_auto_login.py -u "邮箱" -p "密码" -t "TOTP密钥"

# 自动开启 2FA
export NEW_GITHUB_EMAIL="邮箱"
export NEW_GITHUB_PASSWORD="密码"
python github_2fa_setup.py          # 第一次运行，获取设备验证码
python github_2fa_setup.py 123456   # 带验证码运行

# 创建 PAT（极速版，~3秒）
python github_create_pat_fast.py --email x --password x --totp-secret x

# 创建 PAT（浏览器版，可看过程）
python github_create_pat.py --email x --password x --totp-secret x

# 批量创建仓库
python github_toolkit.py repo --token github_pat_xxx --name "proj-{i}" --count 5 --public

# 提交文件
python github_toolkit.py commit --token github_pat_xxx --repo my-repo --file README.md --content "Hello"

# 一键流水线：PAT → 仓库 → 初始化
python github_toolkit.py pipeline --email x --password x --totp-secret x --count 3 --public

# 注册 Devin（通过 GitHub OAuth）
python devin_signup.py --email x --password x --totp-secret x --headless

# 登录 Devin
python devin_signup.py --email x --password x --totp-secret x --signin --headless
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `github_gui.py` | **桌面 GUI 版** — tkinter 图形界面，集成所有功能 |
| `github_auto_login.py` | 命令行登录脚本（纯 HTTP） |
| `github_login_web.py` | Flask 网页 GUI 登录 |
| `github_2fa_setup.py` | 自动开启 GitHub 2FA 并获取 TOTP Secret |
| `github_create_pat.py` | 创建 PAT — Playwright 浏览器自动化版 |
| `github_create_pat_fast.py` | 创建 PAT — 纯 HTTP 极速版（~3秒） |
| `github_toolkit.py` | 完整工具箱 — PAT/仓库/提交/列表/删除/流水线 |
| `devin_signup.py` | Devin 注册/登录（GitHub OAuth 自动化） |
| `启动.bat` | Windows 一键启动 Flask GUI |
| `启动.sh` | Linux/Mac 一键启动 Flask GUI |
| `使用说明.txt` | 使用文档 |
| `requirements.txt` | Python 依赖列表 |

## 依赖

- `requests` — HTTP 请求
- `pyotp` — TOTP 验证码生成
- `flask` — 网页 GUI（仅 `github_login_web.py` 需要）
- `playwright` — 浏览器自动化（PAT 创建、2FA 开启、Devin 注册需要）

## 注意事项

- **安全**: 不要将密码和 TOTP 密钥硬编码在脚本或公开代码中
- **PAT**: Token 创建后只显示一次，务必立即保存
- **TOTP**: 需要的是原始密钥（Base32 字符串），不是 6 位验证码
- **Edge 浏览器**: GUI 版自动检测系统上的 Edge/Chrome 路径，无需手动配置
