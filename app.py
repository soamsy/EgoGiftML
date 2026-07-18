"""Small flask app for browsing ego_gifts
"""

import re
import sqlite3
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, send_from_directory, url_for
from markupsafe import Markup

from manual_trainer import trainer_bp
from migrate import DB_PATH
from tester import tester_bp
from constants import IMAGES_DIR

ROMAN_TABLE = [
    (5, "V"), (4, "IV"), (1, "I"),
]

app = Flask(__name__)
app.register_blueprint(trainer_bp)
app.register_blueprint(tester_bp)


def to_roman(n: int | None) -> str:
    if not n:
        return ""
    result = ""
    for value, numeral in ROMAN_TABLE:
        while n >= value:
            result += numeral
            n -= value
    return result


app.jinja_env.filters["roman"] = to_roman

UPTIE_ICONS = {1: "○", 2: "+", 3: "++"}

# GLL's own markup: #keyword# refers to a status/keyword, $[...]$ marks it's own thing.
# Make it look a bit better
TAG_RE = re.compile(r"(\s\$\[[^\]]+\]\$\s)|(\s\$\[[^\]]+\]\$\$\s)|(\s#[^#\d]+#\s)")


def highlight_tags(text: str | None) -> Markup:
    if not text:
        return Markup("")

    def repl(m: re.Match) -> str:
        bracket, heavy_bracket, hashtag = m.group(1), m.group(2), m.group(3)
        if heavy_bracket:
            return f'<span class="text-tag-bracket">{' ' + heavy_bracket[3:len(heavy_bracket)-4] + ' '}</span>'
        if bracket:
            return f'<span class="text-tag-bracket">{' ' + bracket[3:len(bracket)-3] + ' '}</span>'
        return f'<span class="text-tag-hash">{' ' + hashtag[2:len(hashtag)-2] + ' '}</span>'

    print(text)
    return Markup(TAG_RE.sub(repl, text))


app.jinja_env.filters["highlight_tags"] = highlight_tags


def clean_uptie_text(text: str | None) -> str | None:
    """Ported from AssetScraperWeb.GiftDetailLive.clean/1: GLL stores
    paragraph breaks as literal <br> tags rather than newlines."""
    if text is None:
        return None
    cleaned = (
        text.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n").strip()
    )
    return cleaned or None


def build_uptie_grades(gift: dict) -> list[dict]:
    """Returns only the uptie levels that actually have text, in order."""
    raw = {1: gift["description"], 2: gift["uptie2"], 3: gift["uptie3"]}
    grades = []
    for level, text in raw.items():
        cleaned = clean_uptie_text(text)
        if cleaned is not None:
            grades.append({
                "level": level,
                "icon": UPTIE_ICONS[level],
                "html": str(highlight_tags(cleaned)),
            })
    return grades


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.route("/")
def root():
    return redirect(url_for("tester.index"))


@app.route("/gifts/")
def index():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, affinity, tier, image_path "
        "FROM ego_gifts ORDER BY sort_id, name"
    ).fetchall()

    gifts = [
        {
            "id": row["id"],
            "name": row["name"],
            "affinity": row["affinity"],
            "tier": row["tier"],
            "image_filename": Path(row["image_path"]).name if row["image_path"] else None,
        }
        for row in rows
    ]

    affinities = sorted({gift["affinity"] for gift in gifts if gift["affinity"]})
    tiers = sorted({gift["tier"] for gift in gifts if gift["tier"] is not None})

    return render_template("index.html", gifts=gifts, affinities=affinities, tiers=tiers)


@app.route("/gift/<gift_id>")
def gift_detail(gift_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM ego_gifts WHERE id = ?", (gift_id,)).fetchone()
    if row is None:
        abort(404)

    gift = dict(row)
    gift["image_filename"] = Path(gift["image_path"]).name if gift["image_path"] else None

    ingredients = []
    if gift["ingredients"]:
        ingredient_ids = gift["ingredients"].split(",")
        placeholders = ",".join("?" * len(ingredient_ids))
        rows = conn.execute(
            f"SELECT id, name FROM ego_gifts WHERE id IN ({placeholders})",
            ingredient_ids,
        ).fetchall()
        by_id = {r["id"]: r["name"] for r in rows}
        ingredients = [
            {"id": iid, "name": by_id.get(iid, iid)} for iid in ingredient_ids
        ]

    grades = build_uptie_grades(gift)

    return render_template(
        "gift_detail.html", gift=gift, ingredients=ingredients, grades=grades
    )


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True, port=5400)
