"""
LLC is a localization project for Limbus that scrapes assets from the game's unity bundle.
Rather than scrape from the unity bundle myself, I take their assets and use gift ids from them.
Really, this only gives me information helpful for sorting on the ego gift browser right now.
"""

import json
import re
import sqlite3
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent
LLC_DIR = BASE_DIR / "data" / "llc"
LLC_REPO_URL = "https://github.com/LocalizeLimbusCompany/LocalizeLimbusCompany.git"

APOSTROPHES_RE = re.compile(r"[’‘‛]")

def normalize(name: str) -> str:
    name = APOSTROPHES_RE.sub("'", name)
    name = " ".join(name.split())
    name = name.replace(" :", ":").replace(": ", ":")
    return name.lower()


# Just grabs the EN folder in LLC
def sync() -> None:
    if (LLC_DIR / ".git").exists():
        subprocess.run(
            ["git", "-C", str(LLC_DIR), "pull", "--quiet"],
            check=True,
            capture_output=True,
            text=True,
        )
        return

    LLC_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git", "clone", "--depth=1", "--filter=blob:none", "--sparse",
            LLC_REPO_URL, str(LLC_DIR),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(LLC_DIR), "sparse-checkout", "set", "EN"],
        check=True,
        capture_output=True,
        text=True,
    )


def load_name_map() -> dict[str, int]:
    name_map: dict[str, int] = {}

    for path in sorted((LLC_DIR / "EN").glob("EGOgift*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"Could not parse {path.name}")
            continue

        for entry in data.get("dataList", []):
            gift_id = entry.get("id")
            name = (entry.get("name") or "").strip()
            if isinstance(gift_id, int) and gift_id < 10_000 and name:
                name_map[normalize(name)] = gift_id

    return name_map


def add_original_ids(conn: sqlite3.Connection) -> None:
    try:
        sync()
    except subprocess.CalledProcessError as e:
        print(f"LLC sync failed: {(e.stderr or '').strip() or e}")
        return

    name_map = load_name_map()
    print(f"LLC name map: {len(name_map)} base gifts loaded")

    gifts = conn.execute("SELECT id, name FROM ego_gifts").fetchall()

    matched = [(gid, name) for gid, name in gifts if normalize(name) in name_map]
    unmatched = [(gid, name) for gid, name in gifts if normalize(name) not in name_map]

    conn.executemany(
        "UPDATE ego_gifts SET original_id = ? WHERE id = ?",
        [(name_map[normalize(name)], gid) for gid, name in matched],
    )
    conn.commit()

    print(f"Original IDs: {len(matched)} / {len(gifts)} matched")

    if unmatched:
        print(f"  Unmatched ({len(unmatched)}):")
        for gid, name in unmatched[:15]:
            print(f"    {gid}: {name!r}")
        if len(unmatched) > 15:
            print(f"    … and {len(unmatched) - 15} more")


if __name__ == "__main__":
    from migrate import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    try:
        add_original_ids(conn)
    finally:
        conn.close()
