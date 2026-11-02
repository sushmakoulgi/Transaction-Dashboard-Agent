#!/bin/sh
# Runs before nginx starts (nginx image hook). The bundle is built with placeholders instead of
# the agent's URL and token, so the image holds neither; they come from the container's
# environment: AGENT_API_URL (required) and AGENT_API_TOKEN (empty when the agent has auth off).
# The token still reaches every browser that opens the page: demo use only.
set -eu

if [ -z "${AGENT_API_URL:-}" ]; then
    echo "40-agent-config.sh: AGENT_API_URL is not set" >&2
    exit 1
fi

escape() { printf '%s' "$1" | sed 's/[|&\\]/\\&/g'; }  # for the sed replacement below
url=$(escape "$AGENT_API_URL")
token=$(escape "${AGENT_API_TOKEN:-}")

find /usr/share/nginx/html -type f -name '*.js' -exec \
    sed -i "s|__AGENT_API_URL__|$url|g; s|__AGENT_API_TOKEN__|$token|g" {} +
echo "40-agent-config.sh: agent URL set to $AGENT_API_URL"
