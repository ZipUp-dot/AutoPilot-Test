import asyncio, sys
print(f"Python {sys.version}")

from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://example.com")
        title = await page.title()
        print(f"Title: {title}")
        await browser.close()

try:
    asyncio.run(test())
except Exception as e:
    print(f"Error: {e}")
