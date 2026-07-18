# Not a part of the main app. Just tries to auto label any image in gifts/ folder that
# - matches a name in the gifts.db table
# - isn't labeled yet
#
# Requires the images under resources/gifts to FIRST be detected by the scraper, otherwise gifts.db ui_image_path will be null.
# So, in general you should run the scraper before running this

import sqlite3
from pathlib import Path

from PIL import Image

from manual_trainer import BASE_DIR, load_manual_train, save_manual_train
from migrate import DB_PATH

PADDING = 14


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, name, ui_image_path FROM ego_gifts "
            "WHERE ui_image_path IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    data = load_manual_train()
    gifts_map = data["gifts"]

    added = 0
    skipped_existing = 0
    skipped_missing = 0

    for gift_id, name, ui_image_path in rows:
        try:
            image_key = str(Path(ui_image_path).relative_to("resources"))
        except ValueError:
            print(f"skip {gift_id}: ui_image_path {ui_image_path!r} not under resources/")
            continue

        if image_key in gifts_map:
            skipped_existing += 1
            continue

        full_path = BASE_DIR / ui_image_path
        if not full_path.exists():
            print(f"skip {gift_id}: {full_path} does not exist")
            skipped_missing += 1
            continue

        with Image.open(full_path) as im:
            width, height = im.size

        gifts_map[image_key] = {
            "region": {"x": 0, "y": 0, "w": width, "h": height},
            "gifts": [
                {
                    "id": gift_id,
                    "name": name,
                    "subregion": {
                        "x": PADDING,
                        "y": PADDING,
                        "w": max(0, width - 2 * PADDING),
                        "h": max(0, height - 2 * PADDING),
                    },
                }
            ],
            "ignore_for_training": False,
        }
        added += 1

    save_manual_train(data)
    print(f"Added {added} entries, skipped {skipped_existing} existing, {skipped_missing} missing files")


if __name__ == "__main__":
    main()
