# Flask app for the /trainer url
import json
import sqlite3
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_from_directory
from PIL import Image

from inference import ModelNotFoundError, detect_region, latest_version
from migrate import DB_PATH

BASE_DIR = Path(__file__).parent
RESOURCES_DIR = BASE_DIR / "resources"
MANUAL_TRAIN_PATH = RESOURCES_DIR / "manual_train.json"

IMAGE_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg"}

trainer_bp = Blueprint("trainer", __name__, url_prefix="/trainer")

def find_images() -> list[str]:
    if not RESOURCES_DIR.exists():
        return []
    return sorted(
        p.relative_to(RESOURCES_DIR).as_posix()
        for p in RESOURCES_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def load_manual_train() -> dict:
    if not MANUAL_TRAIN_PATH.exists():
        return {"gifts": {}}
    try:
        data = json.loads(MANUAL_TRAIN_PATH.read_text())
    except json.JSONDecodeError:
        data = {}
    data.setdefault("gifts", {})
    return data


def save_manual_train(data: dict) -> None:
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_TRAIN_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def get_known_gifts() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT id, name FROM ego_gifts ORDER BY name").fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]
    finally:
        conn.close()

def build_name_to_id() -> dict[str, str]:
    return {g["name"].strip().lower(): g["id"] for g in get_known_gifts()}


def is_valid_rect(rect) -> bool:
    return (
        isinstance(rect, dict)
        and all(k in rect for k in ("x", "y", "w", "h"))
        and all(isinstance(rect[k], (int, float)) for k in ("x", "y", "w", "h"))
    )


@trainer_bp.route("/")
def index():
    return render_template(
        "trainer.html",
        images=find_images(),
        manual_data=load_manual_train(),
        known_gifts=get_known_gifts(),
    )


@trainer_bp.route("/image/<path:relpath>")
def serve_trainer_image(relpath):
    return send_from_directory(RESOURCES_DIR, relpath)


@trainer_bp.route("/save", methods=["POST"])
def save():
    payload = request.get_json(silent=True)
    if not payload:
        abort(400, "invalid JSON body")

    image = payload.get("image")
    region = payload.get("region")
    gifts = payload.get("gifts", [])
    ignore_for_training = bool(payload.get("ignore_for_training", False))

    if not image or image not in find_images():
        abort(400, "unknown image")
    if region is not None and not is_valid_rect(region):
        abort(400, "malformed region")
    if not isinstance(gifts, list):
        abort(400, "gifts must be a list")

    name_to_id = build_name_to_id()
    for gift in gifts:
        name = (gift.get("name") or "").strip() if isinstance(gift, dict) else ""
        subregion = gift.get("subregion") if isinstance(gift, dict) else None
        if not name or not is_valid_rect(subregion):
            abort(400, "every gift needs a name and a subregion")
        resolved_id = name_to_id.get(name.lower())
        if not resolved_id:
            abort(400, f"{name!r} does not exactly match a known gift name")
        gift["id"] = resolved_id

    data = load_manual_train()
    data["gifts"][image] = {
        "region": region,
        "gifts": gifts,
        "ignore_for_training": ignore_for_training,
    }
    save_manual_train(data)

    return jsonify(ok=True, entry=data["gifts"][image])


def _round_rect(box: dict, region: dict) -> dict:
    x0 = round(box["x"] - region["x"])
    y0 = round(box["y"] - region["y"])
    x1 = round(box["x"] - region["x"] + box["w"])
    y1 = round(box["y"] - region["y"] + box["h"])
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

@trainer_bp.route("/autodetect", methods=["POST"])
def autodetect():
    payload = request.get_json(silent=True)
    if not payload:
        abort(400, "invalid JSON body")

    image = payload.get("image")
    region = payload.get("region")

    if not image or image not in find_images():
        abort(400, "unknown image")
    if not is_valid_rect(region):
        abort(400, "malformed region")

    detector_version = latest_version("detector")
    classifier_version = latest_version("classifier")
    if not detector_version or not classifier_version:
        abort(400, "no trained models found in models/ -- run train.py first")

    try:
        full_image = Image.open(RESOURCES_DIR / image).convert("RGB")
    except OSError:
        abort(400, "could not open image")

    try:
        results, _timing_ms = detect_region(
            full_image, region, "both",
            {"version": detector_version, "conf": 0.45, "iou": 0.1, "imgsz": 384},
            {"version": classifier_version, "topk": 3, "conf": 0.6},
        )
    except (ValueError, ModelNotFoundError) as e:
        abort(400, str(e))

    gifts = [
        {
            "id": r["gift_id"],
            "name": r["gift_name"],
            "subregion": _round_rect(r, region),
        }
        for r in results
        if r["gift_id"]
    ]

    return jsonify(ok=True, gifts=gifts, detector_version=detector_version, classifier_version=classifier_version)
