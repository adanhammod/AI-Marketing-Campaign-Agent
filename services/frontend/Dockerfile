FROM node:22.22.1-bookworm-slim AS build
WORKDIR /app/services/frontend
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci
COPY services/frontend/ ./
RUN npm run build

FROM nginx:1.29.1-alpine
COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/services/frontend/dist /usr/share/nginx/html
RUN mkdir -p /var/cache/nginx/client_temp \
             /var/cache/nginx/proxy_temp \
             /var/cache/nginx/fastcgi_temp \
             /var/cache/nginx/uwsgi_temp \
             /var/cache/nginx/scgi_temp \
 && chown -R 101:101 /var/cache/nginx \
 && touch /var/run/nginx.pid \
 && chown 101:101 /var/run/nginx.pid
EXPOSE 8080
USER 101
CMD ["nginx", "-g", "daemon off;"]
