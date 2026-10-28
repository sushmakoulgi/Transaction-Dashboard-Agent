# Builds the static UI with Node, then serves it with nginx.
# VITE_* values are compiled into the bundle, so they are build arguments.
FROM node:24-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_AGENT_API_URL=http://localhost:8000
ARG VITE_AGENT_API_TOKEN=
ENV VITE_AGENT_API_URL=$VITE_AGENT_API_URL \
    VITE_AGENT_API_TOKEN=$VITE_AGENT_API_TOKEN
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /ui/dist /usr/share/nginx/html
