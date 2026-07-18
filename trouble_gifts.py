import sqlite3

from migrate import DB_PATH

TROUBLE_GIFTS = {
    "Burial Curse": 3.0,
    "Gathering Skulls": 3.0,
    "Scalpel": 3.0,
    "Endorphin Kit": 2.0,
    "Shadow Bamboo Hat": 2.0,
    "Grandeur": 2.0,
    "The Unchosen": 2.0,
    "Headless Portrait": 2.0,
    "Staticky Two-way Radio": 2.0,
    "Cigarette Holder": 2.0,
    "Torn Bandolier": 2.0,
    "Keenbranch": 2.0,
    "Lightning Axe": 2.0,
    "Moment of Sentencing": 2.0,
    "Rusty Commemorative Coin": 2.0,
    "Tangled Bones": 2.0,
    "Eerie Effigy": 2.0,
    "Contempt of the Gaze of Contempt": 2.0,
    "Glimpse of Flames": 2.0,
    "Stone Tomb": 2.0,
    "Coupled Oscillation": 2.0,
    "Devotion": 2.0,
    "Oil-gunked Spanner": 2.0,
    "Red Tassel": 2.0,
    "Snake Slough": 2.0,
    "Universal Instinct": 2.0,
    "Torn Hems": 2.0,
}


def set_trouble_gifts(conn: sqlite3.Connection) -> None:
    for name, rating in TROUBLE_GIFTS.items():
        cur = conn.execute(
            "UPDATE ego_gifts SET trouble_rating = ? WHERE name = ?", (rating, name)
        )
        if cur.rowcount == 0:
            print(f"trouble_gifts: no gift named {name!r} found, skipped")
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        set_trouble_gifts(conn)
    finally:
        conn.close()
    print(f"trouble_gifts: set {len(TROUBLE_GIFTS)} gift(s)")


if __name__ == "__main__":
    main()
