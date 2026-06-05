#!/usr/bin/env python3
"""
GitHub 2FA TOTP 自动开启脚本
纯协议方式（Playwright CDP），无 GUI 交互
输入：GitHub 邮箱 + 密码 + 设备验证码
输出：邮箱----密码----TOTP_SECRET

健壮性处理：
- 已开启 2FA → 检测并跳过
- 登录失败（密码错误）→ 明确报错
- 设备验证码过期/错误 → 提示重试
- TOTP 验证码过期 → 自动重试（最多3次）
- 需要密码确认 → 自动处理
- 网络超时 → 重试
"""
import asyncio
import sys
import re
import os
import json
import time
import pyotp
from playwright.async_api import async_playwright, TimeoutError as PWTimeout


class GitHubError(Exception):
    pass

class AlreadyEnabled(GitHubError):
    pass

class LoginFailed(GitHubError):
    pass

class DeviceCodeNeeded(GitHubError):
    pass

class DeviceCodeInvalid(GitHubError):
    pass


async def github_enable_2fa(email: str, password: str, device_code: str = ""):
    """
    全自动开启 GitHub 2FA
    Returns: (totp_secret, recovery_codes, username)
    Raises: GitHubError on failure
    """
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:29229")
        context = browser.contexts[0]
        page = await context.new_page()

        try:
            # ===== Step 1: 登录 =====
            print("[1/6] 登录 GitHub...")
            try:
                await page.goto("https://github.com/login", wait_until="networkidle", timeout=15000)
            except PWTimeout:
                print("  网络超时，重试...")
                await page.goto("https://github.com/login", wait_until="networkidle", timeout=30000)

            # 检查是否已经登录（cookie有效）
            if "login" not in page.url:
                print("  已有活跃 session，跳过登录")
            else:
                login_input = await page.query_selector('input[name="login"]')
                if not login_input:
                    raise LoginFailed("找不到登录表单")

                await page.fill('input[name="login"]', email)
                await page.fill('input[name="password"]', password)
                await page.click('input[name="commit"]')
                await page.wait_for_load_state("networkidle")

            url = page.url
            print(f"  -> {url}")

            # ===== Step 2: 检查登录结果 =====
            # 密码错误
            error_text = await page.evaluate('''() => {
                const flash = document.querySelector('.flash-error, [role="alert"]');
                return flash ? flash.textContent.trim() : '';
            }''')
            if error_text and ("incorrect" in error_text.lower() or "invalid" in error_text.lower()):
                raise LoginFailed(f"登录失败：{error_text}")

            # 还在登录页（其他原因失败）
            if page.url.endswith("/session") or ("/login" in page.url and "verified-device" not in page.url):
                page_text = await page.text_content("body")
                if "incorrect" in page_text.lower() or "invalid" in page_text.lower():
                    raise LoginFailed("用户名或密码错误")

            # ===== 已开启 2FA 检测（登录时要求 TOTP）=====
            if "two-factor" in page.url or "two_factor" in page.url:
                # 登录后跳转到 2FA 验证页 = 该账号已开启 2FA
                raise AlreadyEnabled("该账号已开启 2FA（登录时要求输入验证码），无需重复设置")

            # ===== Step 3: 设备验证 =====
            if "verified-device" in page.url:
                print("[2/6] 需要设备验证码...")

                # 提取过期时间
                expire_text = await page.evaluate('''() => {
                    const text = document.body.textContent;
                    const m = text.match(/expire at ([\\d:]+\\s*[AP]M\\s*UTC)/i);
                    return m ? m[1] : '';
                }''')
                if expire_text:
                    print(f"  验证码过期时间: {expire_text}")

                if not device_code:
                    raise DeviceCodeNeeded("需要设备验证码，请查看 QQ 邮箱")

                print(f"  提交验证码: {device_code}")
                await page.fill('input[name="otp"]', device_code)
                await page.click('button:has-text("Verify")')
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)

                # 检查验证码是否有效
                if "verified-device" in page.url:
                    err = await page.evaluate('''() => {
                        const flash = document.querySelector('.flash-error, [role="alert"]');
                        return flash ? flash.textContent.trim() : '';
                    }''')
                    raise DeviceCodeInvalid(f"设备验证码无效或已过期: {err or '请重新获取'}")

                print(f"  -> {page.url}")
            else:
                print("[2/6] 无需设备验证")

            # 最终登录检查
            if "/login" in page.url and "settings" not in page.url:
                raise LoginFailed("登录失败，请检查账密")

            # 获取用户名
            username = await page.evaluate('''() => {
                const meta = document.querySelector('meta[name="user-login"]');
                return meta ? meta.getAttribute('content') : '';
            }''')
            print(f"  用户名: {username}")

            # ===== Step 4: 进入 2FA 设置 =====
            print("[3/6] 进入 2FA 设置页...")
            await page.goto(
                "https://github.com/settings/two_factor_authentication/setup/intro",
                wait_until="networkidle", timeout=15000
            )
            await page.wait_for_timeout(2000)

            current_url = page.url

            # --- 已开启 2FA 检测 ---
            # 情况1: 重定向到 /settings/security（已开启）
            if "/settings/security" in current_url and "setup" not in current_url:
                has_2fa = await page.evaluate('''() => {
                    const text = document.body.textContent;
                    return text.includes('Disable') || text.includes('Authenticator app');
                }''')
                if has_2fa:
                    raise AlreadyEnabled("该账号已开启 2FA，无需重复设置")

            # 情况2: 页面显示"已启用"文本
            page_text = await page.text_content("main") or ""
            if "already enabled" in page_text.lower() or "2fa is now enabled" in page_text.lower():
                raise AlreadyEnabled("该账号已开启 2FA，无需重复设置")

            # 情况3: 需要密码确认（sudo mode）
            if "password" in current_url or "confirm" in current_url:
                print("  需要密码确认...")
                pwd_input = await page.query_selector('input[type="password"]')
                if pwd_input:
                    await pwd_input.fill(password)
                    submit = await page.query_selector('button[type="submit"], input[type="submit"]')
                    if submit:
                        await submit.click()
                        await page.wait_for_load_state("networkidle")
                        await page.wait_for_timeout(2000)

            # 情况4: 重定向到登录页
            if "/login" in page.url:
                raise LoginFailed("Session 已过期，需要重新登录")

            # 确认在 2FA 设置页
            if "two_factor" not in page.url and "setup" not in page.url:
                raise GitHubError(f"未能进入 2FA 设置页，当前: {page.url}")

            # ===== Step 5: 获取 TOTP Secret =====
            print("[4/6] 获取 TOTP Secret...")

            # 点击 "setup key" 按钮
            await page.evaluate('''() => {
                const buttons = document.querySelectorAll('button[type="button"]');
                for (const b of buttons) {
                    if (b.textContent.includes('setup key')) {
                        b.click();
                        return;
                    }
                }
            }''')
            await page.wait_for_timeout(1500)

            # 提取 secret
            totp_secret = await page.evaluate('''() => {
                // dialog > scrollable-region
                const sr = document.querySelector('dialog scrollable-region');
                if (sr) {
                    const m = sr.textContent.trim().match(/[A-Z2-7]{16,}/);
                    if (m) return m[0];
                }
                // dialog 全文
                const dialog = document.querySelector('dialog');
                if (dialog) {
                    const m = dialog.textContent.match(/[A-Z2-7]{16,}/);
                    if (m) return m[0];
                }
                // 页面全文
                const matches = document.body.textContent.match(/[A-Z2-7]{16,32}/g);
                if (matches) {
                    for (const m of matches) {
                        if (m.length >= 16 && m.length <= 32) return m;
                    }
                }
                return null;
            }''')

            if not totp_secret:
                raise GitHubError("无法获取 TOTP Secret")

            print(f"  TOTP Secret: {totp_secret}")

            # 关闭 dialog
            await page.evaluate('''() => {
                const btn = document.querySelector('button[aria-label="Close"]');
                if (btn) btn.click();
            }''')
            await page.wait_for_timeout(500)

            # ===== Step 6: 提交 TOTP 验证码（最多重试3次）=====
            print("[5/6] 提交 TOTP 验证码...")
            success = False

            for attempt in range(3):
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                print(f"  第{attempt+1}次 TOTP Code: {code}")

                await page.fill('input[name="otp"]', code)
                await page.wait_for_timeout(500)

                await page.evaluate('''() => {
                    const buttons = document.querySelectorAll('button');
                    for (const b of buttons) {
                        if (b.textContent.trim().includes('Continue')) {
                            b.disabled = false;
                            b.click();
                            return;
                        }
                    }
                }''')

                await page.wait_for_timeout(3000)
                await page.wait_for_load_state("networkidle")

                content = await page.content()
                if "verification failed" not in content.lower():
                    success = True
                    break

                print("  验证码失败，等待新周期...")
                # 等到下一个 TOTP 周期（30秒制）
                remaining = 30 - (int(time.time()) % 30)
                if remaining < 5:
                    remaining += 30
                await page.wait_for_timeout(remaining * 1000)

            if not success:
                raise GitHubError("TOTP 验证码多次失败，请检查 secret 是否正确")

            # ===== Step 7: 提取恢复码 =====
            print("[6/6] 提取恢复码...")
            content = await page.content()
            recovery_codes = re.findall(r'[a-f0-9]{5}-[a-f0-9]{5}', content)
            recovery_codes = list(dict.fromkeys(recovery_codes))

            if len(recovery_codes) >= 10:
                print(f"  获取 {len(recovery_codes)} 个恢复码")

                # Download
                await page.evaluate('''() => {
                    for (const b of document.querySelectorAll('button')) {
                        if (b.textContent.includes('Download')) { b.click(); break; }
                    }
                }''')
                await page.wait_for_timeout(1000)

                # I have saved
                await page.evaluate('''() => {
                    for (const b of document.querySelectorAll('button')) {
                        if (b.textContent.includes('I have saved')) {
                            b.disabled = false; b.click(); break;
                        }
                    }
                }''')
                await page.wait_for_timeout(2000)

                # Done
                await page.evaluate('''() => {
                    for (const b of document.querySelectorAll('button')) {
                        if (b.textContent.trim() === 'Done') { b.click(); break; }
                    }
                }''')
                await page.wait_for_timeout(1000)
            else:
                print(f"  WARNING: 只找到 {len(recovery_codes)} 个恢复码")

            print(f"  最终页面: {page.url}")
            return totp_secret, recovery_codes, username

        finally:
            await page.close()


async def main():
    email = os.environ.get("NEW_GITHUB_EMAIL", "")
    password = os.environ.get("NEW_GITHUB_PASSWORD", "")
    device_code = sys.argv[1] if len(sys.argv) > 1 else ""

    if not email or not password:
        print("ERROR: 需要设置环境变量 NEW_GITHUB_EMAIL 和 NEW_GITHUB_PASSWORD")
        print("  export NEW_GITHUB_EMAIL='xxx@qq.com'")
        print("  export NEW_GITHUB_PASSWORD='xxx'")
        sys.exit(1)

    if "@" not in email:
        email = email + "@qq.com"

    print(f"{'='*50}")
    print(f"GitHub 2FA 自动开启")
    print(f"账号: {email}")
    print(f"{'='*50}")

    try:
        totp_secret, recovery_codes, username = await github_enable_2fa(email, password, device_code)
    except AlreadyEnabled as e:
        print(f"\nSKIP: {e}")
        print("该账号 2FA 已开启，无需操作。")
        sys.exit(0)
    except LoginFailed as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    except DeviceCodeNeeded:
        print("\nNEED_DEVICE_CODE")
        print("请查看 QQ 邮箱获取 6 位验证码，然后运行:")
        print(f"  python3 {__file__} <验证码>")
        sys.exit(2)
    except DeviceCodeInvalid as e:
        print(f"\nERROR: {e}")
        print("请重新获取验证码后运行:")
        print(f"  python3 {__file__} <新验证码>")
        sys.exit(3)
    except GitHubError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    # 输出结果
    output = f"{email}----{password}----{totp_secret}"
    print(f"\n{'='*50}")
    print(f"RESULT: {output}")
    print(f"{'='*50}")

    with open("github_2fa_result.txt", "w") as f:
        f.write(output + "\n")

    if recovery_codes:
        with open("github_recovery_codes.txt", "w") as f:
            f.write(f"GitHub 2FA Recovery Codes for {email} ({username})\n")
            f.write(f"{'='*40}\n")
            for rc in recovery_codes:
                f.write(rc + "\n")
            f.write(f"{'='*40}\n")
            f.write(f"TOTP Secret: {totp_secret}\n")
        print("恢复码已保存到 github_recovery_codes.txt")

    print("DONE!")


if __name__ == "__main__":
    asyncio.run(main())
