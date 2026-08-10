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

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]