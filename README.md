# GitHubLoginTool

GitHub 自动登录工具 - 支持 TOTP 两步验证，提供命令行和网页 GUI 界面。

## 功能

- 纯 Python `requests` 实现，无需浏览器
- 自动处理 CSRF Token
- 自动获取 TOTP 验证码并完成两步验证
- 支持单账号和批量登录
- 提供命令行和网页 GUI 两种使用方式
- **自动开启 GitHub 2FA** 并获取 TOTP Secret（Playwright CDP 协议）

## 文件说明

| 文件 | 说明 |
|------|------|
| `github_auto_login.py` | 命令行版本（登录已有 2FA 的账号） |
| `github_login_web.py` | 网页 GUI 版本（Flask） |
| `github_2fa_setup.py` | **自动开启 2FA 并获取 TOTP Secret** |
| `启动.bat` | Windows 一键启动脚本 |
| `启动.sh` | Linux/Mac 一键启动脚本 |
| `使用说明.txt` | 使用文档 |

## 快速开始

### 前提条件

- Python 3.8+
- `pip install requests flask playwright pyotp`
- `playwright install chromium`

### 方式一：网页 GUI（推荐）

**Windows**: 双击 `启动.bat`

**Linux/Mac**: 运行 `bash 启动.sh`

然后在浏览器中输入账号信息，格式：`账号----密码----TOTP密钥`

### 方式二：命令行

```bash
python3 github_auto_login.py -u "邮箱" -p "密码" -t "TOTP密钥"
```

批量登录：
```bash
python3 github_auto_login.py -f accounts.json
```

### 方式三：自动开启 2FA

为尚未开启 2FA 的 GitHub 账号自动开启 TOTP 两步验证：

```bash
# 设置环境变量
export NEW_GITHUB_EMAIL="QQ邮箱"
export NEW_GITHUB_PASSWORD="密码"

# 第一次运行（会提示需要设备验证码）
python3 github_2fa_setup.py

# 从邮箱获取验证码后，带参数运行
python3 github_2fa_setup.py 123456
```

**输出格式：** `邮箱----密码----TOTP_SECRET`

**健壮性处理：**
- 已开启 2FA → 自动检测并跳过
- 登录失败（密码错误） → 明确报错
- 设备验证码过期/错误 → 提示重试
- TOTP 验证码过期 → 自动重试（最多 3 次）
- 密码确认页 → 自动处理

**退出码：**
| 代码 | 含义 |
|------|------|
| 0 | 成功（或已开启 2FA） |
| 1 | 错误（密码错误等） |
| 2 | 需要设备验证码 |
| 3 | 设备验证码无效 |

## 依赖

- `requests`
- `flask`（仅网页 GUI 版需要）
- `playwright`（仅 2FA 自动开启需要）
- `pyotp`（仅 2FA 自动开启需要）
