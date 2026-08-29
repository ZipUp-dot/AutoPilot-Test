# ============================================================
# Stage 1: Build — 编译 Vue 3 前端产物
# ============================================================
FROM node:20-alpine AS builder

WORKDIR /app

# 利用 Docker 缓存层：先安装依赖
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

# 复制源码并构建
COPY . .
RUN npm run build

# ============================================================
# Stage 2: Serve — Nginx 提供静态文件 + 反向代理后端
# ============================================================
FROM nginx:alpine

# 内部令牌：构建时注入 nginx 反向代理（与 backend 的 INTERNAL_API_TOKEN 保持一致）
ARG INTERNAL_API_TOKEN=""

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 替换占位符 __INTERNAL_API_TOKEN__ 为构建参数，使 /uploads、/reports 反代附加鉴权头
RUN sed -i "s|__INTERNAL_API_TOKEN__|${INTERNAL_API_TOKEN}|g" /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]