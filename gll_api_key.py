import base64
import json
import re
from urllib.parse import urljoin

import requests

SITE = "https://gll-fun.com"
DEFAULT_TIMEOUT = 100

SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+\.js)"')
# Finds base64 encoded version of
# {"alg":"HS256","typ":"JWT"}{"role":"anon","iss":"supabase"...
JWT_RE = re.compile(r"ey[A-Za-z0-9_-]{10,}\.ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

def decode_jwt_payload(token: str) -> dict | None:
    try:
        _, payload_b64, _ = token.split(".")
    except ValueError:
        return None

    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError):
        return None


def is_supabase_key(token: str) -> bool:
    payload = decode_jwt_payload(token)
    return payload is not None and payload.get("iss") == "supabase"

def find_gll_urls() -> list[str]:
    resp = requests.get(SITE, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return [urljoin(SITE, src) for src in SCRIPT_SRC_RE.findall(resp.text)]

def scrape_api_key() -> str:
    for url in find_gll_urls():
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT)
        if not resp.ok:
            continue
        for candidate in JWT_RE.findall(resp.text):
            if is_supabase_key(candidate):
                return candidate

    raise RuntimeError("Couldn't find a Supabase key from gll-fun")

if __name__ == "__main__":
    print("Supabase key:")
    print(scrape_api_key())
