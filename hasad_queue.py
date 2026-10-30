#!/usr/bin/env python3
"""Small GitHub Actions OIDC client for the centralized Hasad queue."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QUEUE_URL = os.environ.get(
    "HASAD_QUEUE_URL",
    "https://fjxhvexxlhvjagbbutth.supabase.co/functions/v1/hasad-queue",
)


def http_json(url: str, headers: dict[str, str], payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from None
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")
    try:
        value = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        raise RuntimeError("invalid JSON response") from None
    if not isinstance(value, dict):
        raise RuntimeError("invalid response")
    return value


def oidc_token() -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise RuntimeError("GitHub OIDC environment is unavailable")
    separator = "&" if "?" in url else "?"
    url = f"{url}{separator}{urllib.parse.urlencode({'audience': 'hasad-supabase-queue'})}"
    response = urllib.request.Request(url, headers={"Authorization": f"bearer {bearer}"})
    try:
        with urllib.request.urlopen(response, timeout=20) as result:
            value = json.loads(result.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError):
        raise RuntimeError("OIDC token request failed") from None
    token = value.get("value") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("OIDC token was not returned")
    return token


def call(action: str, **payload: object) -> dict:
    token = oidc_token()
    return http_json(
        QUEUE_URL,
        {"content-type": "application/json", "x-github-oidc-token": token},
        {"action": action, **payload},
    )


def output(name: str, value: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "enqueue":
        task_type = required("HASAD_TASK_TYPE")
        result = call(
            "enqueue",
            task_type=task_type,
            link=os.environ.get("TARGET_LINK", "").strip(),
        )
        queue_id = result.get("queue_id")
        if not queue_id:
            raise RuntimeError("queue id was not returned")
        output("queue_id", queue_id)
        print("manual request queued")
        return 0
    if command == "claim":
        result = call(
            "claim",
            queue_id=required("HASAD_QUEUE_ID"),
            fencing_token=required("HASAD_FENCING_TOKEN"),
            task_type=required("HASAD_TASK_TYPE"),
            link=os.environ.get("TARGET_LINK", "").strip(),
        )
        process = result.get("process") is True
        output("process", str(process).lower())
        if process and result.get("link"):
            output("link", result["link"])
        print("queue claim accepted" if process else "queue claim declined")
        return 0
    if command == "heartbeat":
        while True:
            result = call(
                "heartbeat",
                queue_id=required("HASAD_QUEUE_ID"),
                fencing_token=required("HASAD_FENCING_TOKEN"),
            )
            if result.get("accepted") is not True:
                raise RuntimeError("heartbeat was not accepted")
            time.sleep(30)
    if command == "complete":
        success = os.environ.get("HASAD_SUCCESS", "false").lower() == "true"
        result = call(
            "complete",
            queue_id=required("HASAD_QUEUE_ID"),
            fencing_token=required("HASAD_FENCING_TOKEN"),
            success=success,
        )
        if result.get("accepted") is not True:
            raise RuntimeError("completion was not accepted")
        print("queue completion accepted")
        return 0
    raise RuntimeError("usage: hasad_queue.py {enqueue|claim|heartbeat|complete}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"queue helper error: {exc}", file=sys.stderr)
        raise SystemExit(1)
