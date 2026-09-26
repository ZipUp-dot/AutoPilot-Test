"""P2 验收目标：本地真实 Web 应用（saucedemo 本地镜像）

在沙箱网络阻断外部站点的情况下，作为 AutoPilot 的真实执行目标。
镜像 saucedemo 的结构与 selectors（与 make_excel.py 步骤完全一致）：

  - GET  /                         登录页（id=user-name / id=password / id=login-button）
  - POST /login                    校验 standard_user/secret_sauce → 会话 → 302 /inventory.html
  - GET  /inventory.html           商品列表（标题 "Products"，链接 text=商品名）
  - GET  /{slug}.html              商品详情（标题=商品名，id=add-to-cart-{slug}）
  - POST /cart/add                 加购（服务端会话级购物车，真实状态）
  - GET  /cart.html                购物车（class=shopping_cart_link 徽标=数量）
  - GET  /logout                   清除会话 → 302 /

会话采用服务端存储 + Cookie，购物车为真实服务端状态（非静态假数据）。
保护页（inventory/cart/商品详情）无会话时 302 → /。
"""
import html
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"
PORT = 8081

COOKIE_NAME = "p2sid"

USERS = {"standard_user": "secret_sauce"}

PRODUCTS = [
    ("Sauce Labs Backpack", "backpack", "$29.99"),
    ("Sauce Labs Bike Light", "bike-light", "$9.99"),
    ("Sauce Labs Bolt T-Shirt", "bolt-tshirt", "$15.99"),
    ("Sauce Labs Fleece Jacket", "fleece-jacket", "$49.99"),
    ("Sauce Labs Onesie", "onesie", "$7.99"),
    ("Test.allTheThings() T-Shirt (Red)", "red-tshirt", "$15.99"),
]
SLUG_TO_PROD = {slug: (name, price) for name, slug, price in PRODUCTS}

# 服务端会话：sid -> {"user": str, "cart": list[slug]}
_SESSIONS: dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()


def _get_session(sid):
    with _SESSIONS_LOCK:
        return _SESSIONS.get(sid)


def _set_session(sid, session):
    with _SESSIONS_LOCK:
        _SESSIONS[sid] = session


def _del_session(sid):
    with _SESSIONS_LOCK:
        _SESSIONS.pop(sid, None)


def _sid_from_cookie(handler) -> str | None:
    raw = handler.headers.get("Cookie") or ""
    m = re.search(COOKIE_NAME + r"=([^;]+)", raw)
    return m.group(1) if m else None


def _layout(page_title: str, body_html: str, cart_size: int = 0, with_menu: bool = True) -> str:
    cart_link = (
        f'<a class="shopping_cart_link" href="/cart.html">'
        f'<span class="shopping_cart_badge">{cart_size}</span> 🛒</a>'
    )
    menu = ""
    if with_menu:
        menu = (
            '<button id="react-burger-menu-btn" class="bm-burger-button">Menu</button>'
            '<div id="menu_content" class="bm-menu" style="display:none">'
            '<nav id="menu_dropdown_items">'
            '<a id="logout_sidebar_link" href="/logout">Logout</a>'
            '</nav></div>'
            '<script>'
            'document.getElementById("react-burger-menu-btn")'
            '.addEventListener("click",function(){'
            'var m=document.getElementById("menu_content");'
            'm.style.display=(m.style.display==="none")?"block":"none";});'
            '</script>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{html.escape(page_title)}</title>
<style>
body{{font-family:Arial,sans-serif;margin:24px}}
.bm-menu{{position:absolute;top:40px;left:8px;background:#fff;border:1px solid #ddd;padding:8px}}
.inventory_item{{border:1px solid #eee;padding:12px;margin:8px 0}}
.btn{{padding:6px 12px}}
</style>
</head><body>
<div id="header_container">
{menu}
{cart_link}
<h1>{html.escape(page_title)}</h1>
</div>
{body_html}
</body></html>"""


def _login_page(msg: str = "") -> str:
    err = f'<p style="color:red">{html.escape(msg)}</p>' if msg else ""
    return _layout("Swag Labs", f"""
<form id="login_button_container" method="post" action="/login">
<input id="user-name" name="user-name" placeholder="Username"><br>
<input id="password" name="password" type="password" placeholder="Password"><br>
<button id="login-button" class="btn" type="submit">Login</button>
</form>
{err}
""", cart_size=0, with_menu=False)


def _inventory_page(session) -> str:
    items = "".join(
        f'<div class="inventory_item">'
        f'<a class="inventory_item_name" href="/{slug}.html">{html.escape(name)}</a> '
        f'<span class="inventory_item_price">{price}</span></div>'
        for name, slug, price in PRODUCTS
    )
    return _layout("Products", items, cart_size=len(session["cart"]))


def _product_page(slug, session) -> str:
    prod = SLUG_TO_PROD.get(slug)
    if prod is None:
        return None
    name, price = prod
    body = (
        f'<h2 class="inventory_details_name">{html.escape(name)}</h2>'
        f'<div class="inventory_details_price">{price}</div>'
        f'<form method="post" action="/cart/add">'
        f'<input type="hidden" name="slug" value="{slug}">'
        f'<button id="add-to-cart-{slug}" class="btn" type="submit">Add to cart</button>'
        f'</form>'
    )
    return _layout(name, body, cart_size=len(session["cart"]))


def _cart_page(session) -> str:
    cart = session["cart"]
    if not cart:
        body = '<div id="cart_contents_container"><p>Your cart is empty</p></div>'
    else:
        rows = "".join(
            f'<div class="cart_item"><span class="inventory_item_name">{html.escape(name)}</span> '
            f'<span class="item_price">{price}</span></div>'
            for slug in cart for (name, price) in [SLUG_TO_PROD[slug]]
        )
        body = f'<div id="cart_contents_container">{rows}</div>'
    return _layout("Your Cart", body, cart_size=len(cart))


class Handler(BaseHTTPRequestHandler):
    server_version = "P2Site/1.0"

    def _send(self, body: bytes, status: int = 200, set_cookie: str | None = None,
              location: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def _session_or_none(self):
        sid = _sid_from_cookie(self)
        return _get_session(sid) if sid else None

    # ── GET ──

    def do_GET(self):
        path = urlparse(self.path).path
        session = self._session_or_none()

        if path == "/" or path == "/index.html":
            self._send(_login_page().encode("utf-8"))
            return
        if path == "/logout":
            sid = _sid_from_cookie(self)
            if sid:
                _del_session(sid)
            self._send(b"", status=302, location="/")
            return
        if not session:
            self._send(b"", status=302, location="/")
            return

        if path == "/inventory.html":
            self._send(_inventory_page(session).encode("utf-8"))
            return
        if path == "/cart.html":
            self._send(_cart_page(session).encode("utf-8"))
            return
        slug = path.strip("/").removesuffix(".html")
        if slug in SLUG_TO_PROD:
            page = _product_page(slug, session)
            if page is not None:
                self._send(page.encode("utf-8"))
                return

        self._send(b"404 Not Found", status=404)

    # ── POST ──

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        fields = parse_qs(body)

        if path == "/login":
            user = (fields.get("user-name") or [""])[0]
            pwd = (fields.get("password") or [""])[0]
            if USERS.get(user) == pwd:
                sid = uuid.uuid4().hex
                _set_session(sid, {"user": user, "cart": []})
                self._send(b"", status=302, set_cookie=f"{COOKIE_NAME}={sid}; Path=/",
                           location="/inventory.html")
            else:
                self._send(_login_page("Username and password do not match").encode("utf-8"))
            return
        if path == "/cart/add":
            session = self._session_or_none()
            slug = (fields.get("slug") or [""])[0]
            if session and slug in SLUG_TO_PROD and slug not in session["cart"]:
                session["cart"].append(slug)
            referer = self.headers.get("Referer") or "/inventory.html"
            self._send(b"", status=302, location=referer)
            return

        self._send(b"404 Not Found", status=404)

    def log_message(self, fmt, *args):  # 静默访问日志，减少刷屏
        pass


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"P2 local site ready: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
