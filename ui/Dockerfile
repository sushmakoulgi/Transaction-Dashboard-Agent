# Builds the static UI with Node, then serves it with nginx.
#
# The agent's URL and token are not baked in: the bundle is built with placeholders, which
# docker-entrypoint.d/40-agent-config.sh replaces at container start from AGENT_API_URL and
# AGENT_API_TOKEN. One image for every environment, and no token in the image or registry.
# The token still reaches every browser that opens the page: demo / single-user use only.
FROM node:24-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV VITE_AGENT_API_URL=__AGENT_API_URL__ \
    VITE_AGENT_API_TOKEN=__AGENT_API_TOKEN__
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-agent-config.sh /docker-entrypoint.d/40-agent-config.sh
COPY --from=build /ui/dist /usr/share/nginx/html
