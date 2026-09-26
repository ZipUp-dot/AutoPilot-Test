"""URL 构建 — target_url + test_path 拼接的唯一入口

P1-1：元素抓取 / AI Prompt / 执行前健康检查 / 执行 goto / 自愈导航的 URL
统一经本函数构建，禁止在各调用点自行拼接（防止口径漂移）。

两个生命周期上下文（调用方各自取值）：
  - Admission 前：传 project.target_url + project.test_path（当前项目值）
  - Admission 后：传 manifest["target_url"] + manifest["test_path"]（Admission 冻结快照）

本函数只接收两个字符串，禁止接收 ORM project 对象。
"""


def build_target_url(target_url: str, test_path: str) -> str:
    """拼接目标访问 URL。

    Args:
        target_url: 项目根地址（如 https://example.com）
        test_path: 子路径（如 /login 或 login）

    Returns:
        完整 URL：target_url.rstrip('/') + '/' + test_path.lstrip('/')
    """
    base = (target_url or "").strip()
    path = (test_path or "").strip()
    return base.rstrip("/") + "/" + path.lstrip("/")
