# GitHubLoginTool

GitHub 自动登录工具 - 支持 TOTP 两步验证，提供命令行和网页 GUI 界面。

## 功能

- 纯 Python `requests` 实现，无需浏览器
- 自动处理 CSRF Token
- 自动获取 TOTP 验证码并完成两步验证
- 支持单账号和批量登录
- 提供命令行和网页 GUI 两种使用方式

## 文件说明

| 文件 | 说明 |
|------|------|
| `github_auto_login.py` | 命令行版本 |
| `github_login_web.py` | 网页 GUI 版本（Flask） |
| `启动.bat` | Windows 一键启动脚本 |
| `启动.sh` | Linux/Mac 一键启动脚本 |
| `使用说明.txt` | 使用文档 |

## 快速开始

### 前提条件

- Python 3.8+
- `pip install requests flask`

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

## 依赖

- `requests`
- `flask`（仅网页 GUI 版需要）
