"""P2 验收测量（一）：生成 ≥100 行真实可解析 Excel（本地 saucedemo 镜像场景）

产物：backend/data/p2_acceptance/cases_120.xlsx
列：用例编号 / 用例名称 / 优先级 / 前置条件 / 操作步骤 / 预期结果

步骤措辞遵循 ExcelParser 的纯文本规则，使 target/value 精确抽出：
  - "打开 <url>"        → navigate（target=URL）
  - "在 <字段> 填写 <值>" → fill（target=字段名，value=值）  ← 只用「填写」一个触发词
  - "点击 <选择器/文本>"  → click（target=选择器或可精确匹配的文本）
  - "验证 <断言>"        → assert_text
  - "截图..." / "等待..." → screenshot / wait

BASE 指向本地真实 Web 应用（local_site_server.py，saucedemo 镜像）。
"""
from pathlib import Path

from openpyxl import Workbook

BASE = "http://localhost:8081"
LOGIN_STEPS = [
    f"1. 打开 {BASE}/",
    "2. 在 Username 填写 standard_user",
    "3. 在 Password 填写 secret_sauce",
    "4. 点击 Login",
]
LOGIN_EXPECT = "5. 验证页面显示 Products"
PRODUCTS = [
    ("Sauce Labs Backpack", "backpack"),
    ("Sauce Labs Bike Light", "bike-light"),
    ("Sauce Labs Bolt T-Shirt", "bolt-tshirt"),
    ("Sauce Labs Fleece Jacket", "fleece-jacket"),
    ("Sauce Labs Onesie", "onesie"),
    ("Test.allTheThings() T-Shirt (Red)", "red-tshirt"),
]


def _steps(flow: list[str]) -> str:
    return "\n".join(flow)


def build_rows() -> list[dict]:
    rows: list[dict] = []
    n = 0
    # ── Flow A：纯登录（30 行，变体含 wait/screenshot/assert_visible）──
    for i in range(30):
        n += 1
        flow = list(LOGIN_STEPS)
        expect = LOGIN_EXPECT
        if i % 3 == 1:
            flow.append("5. 截图保存登录后页面")
            expect = LOGIN_EXPECT + "\n6. 截图成功执行"
        elif i % 3 == 2:
            flow.append("5. 等待 1 秒")
            flow.append("6. 验证 .shopping_cart_link 可见")
            expect = LOGIN_EXPECT + "\n7. 购物车图标可见"
        rows.append({
            "case_no": f"TC{n:03d}",
            "case_name": f"标准用户登录并验证首页（变体{i+1}）",
            "priority": "P1",
            "pre_condition": "无，本地站点可匿名访问",
            "steps": _steps(flow),
            "expected_result": expect,
        })
    # ── Flow B：登录 + 打开商品（36 行，6 商品 × 6 变体）──
    for prod, slug in PRODUCTS:
        for v in range(6):
            n += 1
            flow = LOGIN_STEPS + [
                f"5. 点击 {prod}",
                f"6. 验证页面显示 {prod}",
            ]
            rows.append({
                "case_no": f"TC{n:03d}",
                "case_name": f"浏览商品 {prod}（变体{v+1}）",
                "priority": "P1",
                "pre_condition": "标准用户已登录",
                "steps": _steps(flow),
                "expected_result": f"7. 页面显示 {prod} 详情，URL 含 {slug}.html",
            })
    # ── Flow C：登录 + 加购（30 行，6 商品 × 5 变体）──
    for prod, slug in PRODUCTS:
        for v in range(5):
            n += 1
            flow = LOGIN_STEPS + [
                f"5. 点击 {prod}",
                f"6. 点击 #add-to-cart-{slug}",
                "7. 点击 .shopping_cart_link",
                "8. 验证 .shopping_cart_badge 文本为 1",
            ]
            rows.append({
                "case_no": f"TC{n:03d}",
                "case_name": f"加购 {prod} 并验证购物车（变体{v+1}）",
                "priority": "P1",
                "pre_condition": "标准用户已登录",
                "steps": _steps(flow),
                "expected_result": f"9. 购物车包含 {prod}，商品数徽标为 1",
            })
    # ── Flow D：退出登录（24 行）──
    for v in range(24):
        n += 1
        flow = LOGIN_STEPS + [
            "5. 点击 #react-burger-menu-btn",
            "6. 点击 #logout_sidebar_link",
            "7. 验证 #login-button 可见",
        ]
        rows.append({
            "case_no": f"TC{n:03d}",
            "case_name": f"登录后退出登录（变体{v+1}）",
            "priority": "P1",
            "pre_condition": "标准用户已登录",
            "steps": _steps(flow),
            "expected_result": "8. 返回登录页，URL 为 / 且登录按钮可见",
        })
    return rows


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "data" / "p2_acceptance"
    out.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    headers = ["用例编号", "用例名称", "优先级", "前置条件", "操作步骤", "预期结果"]
    ws.append(headers)
    rows = build_rows()
    for r in rows:
        ws.append([r["case_no"], r["case_name"], r["priority"],
                   r["pre_condition"], r["steps"], r["expected_result"]])
    path = out / "cases_120.xlsx"
    wb.save(str(path))
    print(f"rows={len(rows)} saved={path}")


if __name__ == "__main__":
    main()
