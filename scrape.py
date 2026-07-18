# All starts here.
# `uv run scrape.py` sets up a sqlite db with ego gift information so app.py can actually see something to load its ui with
# Doesn't train any models. Just populates ego gift information

import argparse
import logging
import os
import re
import sqlite3
from math import ceil
from pathlib import Path

import requests

import llc
from gll_api_key import scrape_api_key
from migrate import DB_PATH, main as run_migrations
from trouble_gifts import set_trouble_gifts

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "data" / "images"
UI_IMAGES_DIR = BASE_DIR / "resources" / "Gift"

API_BASE = "https://admin.gll-fun.com/rest/v1"
IMAGE_BASE = "https://gll-fun.com/images/md-gifts"

IGNORED_IDS = {"gift_0"}
IGNORED_NAMES = {"Sea Terror's Notes"}

INGREDIENT_RE = re.compile(r"#(gift_\d+)#")

UPSERT_SQL = """
INSERT INTO ego_gifts
    (id, name, sin, affinity, tier, buying_cost, selling_cost,
     obtain_method, description, uptie2, uptie3, ingredients, image_path)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name = excluded.name,
    sin = excluded.sin,
    affinity = excluded.affinity,
    tier = excluded.tier,
    buying_cost = excluded.buying_cost,
    selling_cost = excluded.selling_cost,
    obtain_method = excluded.obtain_method,
    description = excluded.description,
    uptie2 = excluded.uptie2,
    uptie3 = excluded.uptie3,
    ingredients = excluded.ingredients,
    image_path = excluded.image_path
"""


def fetch_all_gifts(limit: int | None, api_key: str) -> list[dict]:
    params = {"select": "*", "apikey": api_key}
    if limit:
        params["limit"] = limit
    resp = requests.get(f"{API_BASE}/md_gifts", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_image(gift_id: str) -> bytes | None:
    url = f"{IMAGE_BASE}/{gift_id}.webp"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as e:
        log.warning("Image %s: %s", gift_id, e)
        return None

    if resp.status_code == 200 and resp.content:
        return resp.content

    log.warning("Image %s: HTTP %s", gift_id, resp.status_code)
    return None


def parse_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_cost(raw: str | None) -> tuple[int | None, int | None]:
    if raw is None:
        return None, None

    parts = [p.strip() for p in raw.strip().split(",")]

    if parts == [""]:
        return None, None

    if len(parts) == 1:
        n = parse_int(parts[0])
        sell = ceil(n / 2) if n is not None else None
        return n, sell

    if len(parts) == 2:
        a = parse_int(parts[0])
        b = parse_int(parts[1])
        return (None, b) if a == 0 else (a, b)

    return None, None


def parse_ingredients(text: str | None) -> str | None:
    if text is None:
        return None
    ids = INGREDIENT_RE.findall(text)
    return ",".join(ids) if ids else None


def ensure_image(gift_id: str) -> str | None:
    """Downloads the gift's image to disk if missing, returns its path
    relative to the project root (or None if unavailable)."""
    img_path = IMAGES_DIR / f"{gift_id}.webp"

    if not img_path.exists():
        image_bytes = download_image(gift_id)
        if image_bytes is None:
            return None
        img_path.write_bytes(image_bytes)

    return str(img_path.relative_to(BASE_DIR))


def upsert_gift(conn: sqlite3.Connection, gift: dict, image_path: str | None) -> None:
    gift_id = gift["id"]
    name = (gift.get("nameEN") or gift_id).strip()
    buying_cost, selling_cost = parse_cost(gift.get("cost"))
    obtain = gift.get("obtainEN")

    conn.execute(
        UPSERT_SQL,
        (
            gift_id,
            name,
            gift.get("sin"),
            gift.get("keyword"),
            gift.get("tier"),
            buying_cost,
            selling_cost,
            obtain,
            gift.get("grade1EN"),
            gift.get("grade2EN"),
            gift.get("grade3EN"),
            parse_ingredients(obtain),
            image_path,
        ),
    )


def print_stats(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM ego_gifts").fetchone()[0]
    with_img = conn.execute(
        "SELECT COUNT(*) FROM ego_gifts WHERE image_path IS NOT NULL"
    ).fetchone()[0]
    has_buy = conn.execute(
        "SELECT COUNT(*) FROM ego_gifts WHERE buying_cost IS NOT NULL"
    ).fetchone()[0]
    has_sell = conn.execute(
        "SELECT COUNT(*) FROM ego_gifts WHERE selling_cost IS NOT NULL"
    ).fetchone()[0]
    has_fuse = conn.execute(
        "SELECT COUNT(*) FROM ego_gifts WHERE ingredients IS NOT NULL"
    ).fetchone()[0]

    print("\n" + "─" * 50)
    print(f"Stored     : {total} gifts")
    print(f"Images     : {with_img} / {total}")
    print(f"Buy cost   : {has_buy} / {total}")
    print(f"Sell cost  : {has_sell} / {total}")
    print(f"Fusion     : {has_fuse} (have ingredients)")


def run(limit: int | None) -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    api_key = scrape_api_key()

    log.info("Fetching gift metadata (limit=%s)…", limit or "all")
    gifts = fetch_all_gifts(limit, api_key)
    log.info("Got %d gifts from API", len(gifts))

    conn = sqlite3.connect(DB_PATH)
    try:
        total = len(gifts)
        for i, gift in enumerate(gifts, 1):
            gift_id = gift["id"]
            name = (gift.get("nameEN") or gift_id).strip()

            if gift_id in IGNORED_IDS or name in IGNORED_NAMES:
                log.debug("[%d/%d] %s — skipped", i, total, gift_id)
                continue

            log.info("[%d/%d] %s  %s", i, total, gift_id, name)
            image_path = ensure_image(gift_id)
            upsert_gift(conn, gift, image_path)
            conn.commit()

        print_stats(conn)
    finally:
        conn.close()

def add_orig_ids() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        llc.add_original_ids(conn)
    finally:
        conn.close()

NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

def normalize_ui_name(name: str) -> str:
    return NON_ALNUM_RE.sub("", name.lower())

def match_ui_images(conn: sqlite3.Connection) -> None:
    if not UI_IMAGES_DIR.exists():
        log.warning("%s not found, skipping ui_image_path matching", UI_IMAGES_DIR)
        return

    name_to_id = {
        normalize_ui_name(name): gift_id
        for gift_id, name in conn.execute("SELECT id, name FROM ego_gifts")
    }

    webp_files = sorted(UI_IMAGES_DIR.rglob("*.webp"))
    matched = 0

    for path in webp_files:
        gift_id = name_to_id.get(normalize_ui_name(path.stem))
        if gift_id is None:
            continue
        conn.execute(
            "UPDATE ego_gifts SET ui_image_path = ? WHERE id = ?",
            (str(path.relative_to(BASE_DIR)), gift_id),
        )
        matched += 1

    conn.commit()
    print(f"UI images  : {matched} / {len(webp_files)} matched")


def match_ui_images_standalone() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        match_ui_images(conn)
    finally:
        conn.close()


def apply_trouble_gifts() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        set_trouble_gifts(conn)
    finally:
        conn.close()

def main() -> None:
    # Ensure db is ready to go
    run_migrations()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="fetch only N gifts")
    args = parser.parse_args()

    run(args.limit)
    add_orig_ids()
    match_ui_images_standalone()
    apply_trouble_gifts()

if __name__ == "__main__":
    main()
