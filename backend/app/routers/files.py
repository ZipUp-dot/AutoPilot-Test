"""受控文件访问 — 替换 StaticFiles，INTERNAL_API_TOKEN 鉴权 + 路径安全 + 流式返回

过渡方案（无 User/RBAC）：
  Authorization: Bearer <INTERNAL_API_TOKEN> 校验
  → 路径规范化（拒绝 ../、绝对路径、空段）
  → 目录边界校验（resolve 解析符号链接后必须在允许目录内）
  → 资源 ID 校验（reports 文件名必须为 execution_{id}_report.html）
  → 文件存在性校验（404）
  → StreamingResponse 流式返回

V2.0 升级路径：User → Project → Permission → File（本阶段不实现 RBAC）。
"""

import hmac
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.config import settings
from app.exceptions import NotFoundException, UnauthorizedException

logger = logging.getLogger("autopilot.files")

router = APIRouter(tags=["文件访问"])

# 报告文件命名：execution_{id}_report.html（资源 ID = execution_id）
REPORT_PATH_RE = re.compile(r"^execution_(\d+)_report\.html$")

# 上传类型子目录（内含资源 ID 段）
UPLOAD_TYPE_DIRS = frozenset({"screenshots", "videos", "excels"})


# ═══════════════════════════════════════════════
# 鉴权
# ═══════════════════════════════════════════════

def require_internal_token(
    authorization: str = Header("", alias="Authorization"),
) -> None:
    """校验 Authorization: Bearer <INTERNAL_API_TOKEN>

    未配置令牌时（开发/测试环境）放行并告警；生产环境启动时已强制配置。
    """
    expected = settings.INTERNAL_API_TOKEN
    if not expected:
        logger.warning("INTERNAL_API_TOKEN 未配置，文件访问接口未启用令牌鉴权（仅限非生产环境）")
        return

    provided = ""
    if authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise UnauthorizedException("无效的文件访问令牌")


# ═══════════════════════════════════════════════
# 路径安全 + 资源校验
# ═══════════════════════════════════════════════

def _safe_resolve(base: Path, rel: str) -> Optional[Path]:
    """路径规范化 + 目录边界校验

    拒绝：绝对路径（前导斜杠）、空路径、空段（双斜杠）、. 与 .. 段。
    resolve() 会解析符号链接，因此 symlink 指向目录外时边界校验失败 → 拒绝。
    """
    norm = rel.replace("\\", "/")
    if not norm or norm.startswith("/"):
        return None
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None

    base_res = base.resolve()
    try:
        target = (base_res / norm).resolve()
        target.relative_to(base_res)
    except (ValueError, OSError):
        return None
    return target


def _validate_resource(kind: str, rel: str) -> bool:
    """资源 ID 校验：路径结构必须符合已知资源模式

    - reports: 严格为 execution_{id}_report.html（资源 ID = execution_id）
    - uploads: 类型子目录（screenshots/videos/excels）下必须含正整数资源 ID 段；
               根目录单文件放行（目录边界已保证安全）
    """
    parts = rel.replace("\\", "/").strip("/").split("/")
    if kind == "reports":
        return len(parts) == 1 and bool(REPORT_PATH_RE.match(parts[0]))
    if parts and parts[0] in UPLOAD_TYPE_DIRS:
        return any(p.isdigit() for p in parts[1:])
    return len(parts) == 1


# ═══════════════════════════════════════════════
# 文件服务
# ═══════════════════════════════════════════════

_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _serve_file(base_dir: str, kind: str, file_path: str) -> StreamingResponse:
    """受控文件服务：路径校验 → 资源 ID 校验 → 存在性 → 流式返回"""
    target = _safe_resolve(Path(base_dir), file_path)
    if target is None:
        raise NotFoundException("文件不存在")

    if not _validate_resource(kind, file_path):
        raise NotFoundException("非法资源路径")

    if not target.is_file():
        raise NotFoundException("文件不存在")

    media_type = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")

    def _iter():
        with open(target, "rb") as f:
            yield from f

    return StreamingResponse(
        _iter(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{target.name}"',
            # 报告 HTML 是内联展示的自包含文档；inline 仅是展示方式，不作为安全边界。
            # 安全依赖：模板 autoescape + </script> 转义 + CSP（meta 兜底离线，此头兜底在线）。
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'none'; font-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            ),
        },
    )


@router.get("/uploads/{file_path:path}", dependencies=[Depends(require_internal_token)])
def get_upload_file(file_path: str):
    """受控访问上传文件（截图 / Excel 等）"""
    return _serve_file(settings.UPLOAD_DIR, "uploads", file_path)


@router.get("/reports/{file_path:path}", dependencies=[Depends(require_internal_token)])
def get_report_file(file_path: str):
    """受控访问报告 HTML"""
    return _serve_file(settings.REPORT_DIR, "reports", file_path)
