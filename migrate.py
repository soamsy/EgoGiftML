# Applies all SQL migration scripts located in `migrations` subdirectory
# Order matters, apply all in sorted order

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DB_PATH = Path(__file__).parent / "data" / "gifts.db"

def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY)"
        )
        applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,)
            )
            conn.commit()
            print(f"applied {path.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
