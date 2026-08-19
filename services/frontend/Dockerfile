FROM node:22.22.1-bookworm-slim AS build
WORKDIR /app/services/frontend
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN npm ci
COPY services/frontend/ ./
RUN npm run build

FROM nginx:1.29.1-alpine
COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/services/frontend/dist /usr/share/nginx/html
EXPOSE 8080
USER 101
CMD ["nginx", "-g", "daemon off;"]
