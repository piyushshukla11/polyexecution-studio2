# Multi-stage build for React 18 + Vite frontend
FROM node:20-alpine AS builder

WORKDIR /app
COPY client/package*.json ./
RUN npm ci

COPY client/ ./
RUN npm run build

# Lightweight Nginx serving built static assets
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/client/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:80/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
