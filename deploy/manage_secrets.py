"""Secrets for the AWS deployment, kept out of terminals, logs and chat.

    uv run python deploy/manage_secrets.py rotate-tokens    # make rotate-tokens
    uv run python deploy/manage_secrets.py push             # make push-secrets

rotate-tokens: new random AGENT_API_TOKEN and TRANSACTION_SERVICE_API_TOKEN in .env, and the
UI's matching VITE_AGENT_API_TOKEN in ui/.env.local. Nothing is printed.

push: copies values from .env into the agent service's AWS Secrets Manager secret (a JSON
object, read through SETTINGS_SECRET_ID). The Transaction Service keeps no secret there: its
token is an environment variable of its Lambda. Prints names and version ids only.
"""

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

import boto3
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
UI_ENV_FILE = ROOT / "ui" / ".env.local"

SECRETS = {
    "transaction-dashboard/agent-service": [
        "ANTHROPIC_API_KEY",
        "AGENT_API_TOKEN",
        "TRANSACTION_SERVICE_API_TOKEN",
    ],
}


def set_variable(path: Path, name: str, value: str) -> None:
    """Replaces `NAME=...` in a dotenv file (or appends it), leaving every other line as is."""
    text = path.read_text() if path.exists() else ""
    line = f"{name}={value}"
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    text = (
        pattern.sub(line, text, count=1) if pattern.search(text) else f"{text.rstrip()}\n{line}\n"
    )
    path.write_text(text)


def rotate_tokens() -> None:
    agent_token = secrets.token_urlsafe(32)
    set_variable(ENV_FILE, "AGENT_API_TOKEN", agent_token)
    set_variable(ENV_FILE, "TRANSACTION_SERVICE_API_TOKEN", secrets.token_urlsafe(32))
    set_variable(UI_ENV_FILE, "VITE_AGENT_API_TOKEN", agent_token)
    print(
        f"New AGENT_API_TOKEN and TRANSACTION_SERVICE_API_TOKEN in {ENV_FILE.name}, matching "
        f"VITE_AGENT_API_TOKEN in {UI_ENV_FILE.relative_to(ROOT)}. Next: make push-secrets"
    )


def push(profile: str | None, region: str) -> None:
    env = dotenv_values(ENV_FILE)
    client = boto3.Session(profile_name=profile, region_name=region).client("secretsmanager")
    for secret_name, keys in SECRETS.items():
        missing = [key for key in keys if not env.get(key)]
        if missing:
            sys.exit(f"{ENV_FILE.name} has no value for {', '.join(missing)}; nothing pushed")
        value = json.dumps({key: env[key] for key in keys})
        try:
            client.describe_secret(SecretId=secret_name)
        except client.exceptions.ResourceNotFoundException:
            created = client.create_secret(
                Name=secret_name,
                SecretString=value,
                Description=f"Settings for {secret_name.split('/')[-1]} (keys: {', '.join(keys)})",
                Tags=[{"Key": "project", "Value": "transaction-dashboard"}],
            )
            print(f"created {secret_name}  {created['ARN']}  version {created['VersionId']}")
        else:
            updated = client.put_secret_value(SecretId=secret_name, SecretString=value)
            print(f"updated {secret_name}  version {updated['VersionId']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rotate-tokens")
    push_parser = commands.add_parser("push")
    push_parser.add_argument("--profile", default=None)
    push_parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    if args.command == "rotate-tokens":
        rotate_tokens()
    else:
        push(args.profile, args.region)


if __name__ == "__main__":
    main()
