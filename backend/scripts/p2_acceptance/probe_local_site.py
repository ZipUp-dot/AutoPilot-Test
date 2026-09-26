"""P2 探针：验证 Playwright 可访问本地站点（localhost:8080），且 URL host 保持 localhost"""
import asyncio
import os

os.environ["TOOLHOST_SANDBOX_DISABLED"] = "true"

URL = "http://localhost:8081/"


async def main():
    from playwright.async_api import async_playwright
    from app.utils.url_policy import UrlPolicy, install_network_policy

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080}, service_workers="block")
        policy = UrlPolicy(URL, config_json={"allowed_ports": [8081]})
        await install_network_policy(context, policy)
        page = await context.new_page()
        print("policy target_host =", policy.target_host, "allowed_ports =", sorted(policy._allowed_ports))
        try:
            await page.goto(URL, wait_until="networkidle", timeout=15000)
            print("goto OK, page.url =", page.url)
            print("title =", await page.title())
            has_user = await page.locator("#user-name").count()
            has_login = await page.locator("#login-button").count()
            print("login form fields present:", has_user, has_login)
            # 填表并提交登录
            await page.fill("#user-name", "standard_user")
            await page.fill("#password", "secret_sauce")
            await page.click("#login-button")
            await page.wait_for_load_state("networkidle")
            print("after login page.url =", page.url)
            print("after login title =", await page.title())
            # 进入商品详情 + 加购 + 购物车
            await page.click("a.inventory_item_name:has-text('Sauce Labs Backpack')")
            await page.wait_for_load_state("networkidle")
            print("product page.url =", page.url)
            await page.click("#add-to-cart-backpack")
            await page.wait_for_load_state("networkidle")
            await page.click(".shopping_cart_link")
            await page.wait_for_load_state("networkidle")
            print("cart page.url =", page.url, "title =", await page.title())
            badge = await page.locator(".shopping_cart_badge").inner_text()
            print("cart badge =", badge)
            # 菜单 → 退出登录
            await page.click("#react-burger-menu-btn")
            await page.click("#logout_sidebar_link")
            await page.wait_for_load_state("networkidle")
            print("after logout page.url =", page.url)
            print("RESULT: OK")
        except Exception as e:
            print("RESULT: FAIL", repr(e))
        await browser.close()


asyncio.run(main())
