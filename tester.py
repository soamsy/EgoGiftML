# Flask app. Verifies trained models work. Visit at /tester
from flask import Blueprint, abort, jsonify, render_template, request, send_from_directory
from PIL import Image

import inference
from inference import ModelNotFoundError, list_versions
from manual_trainer import RESOURCES_DIR, find_images, is_valid_rect, load_manual_train

tester_bp = Blueprint("tester", __name__, url_prefix="/tester")

@tester_bp.route("/")
def index():
    return render_template(
        "tester.html",
        images=find_images(),
        manual_data=load_manual_train(),
        detector_versions=list_versions("detector"),
        classifier_versions=list_versions("classifier"),
    )


@tester_bp.route("/image/<path:relpath>")
def serve_tester_image(relpath):
    return send_from_directory(RESOURCES_DIR, relpath)


@tester_bp.route("/evaluate", methods=["POST"])
def evaluate():
    """Runs detector+classifier against every region+gifts entry in manual_train.json"""
    payload = request.get_json(silent=True) or {}
    det_cfg = dict(payload.get("detector") or {})
    det_cfg.setdefault("version", None)
    det_cfg["version"] = det_cfg["version"] or inference.latest_version("detector")
    cls_cfg = dict(payload.get("classifier") or {})
    cls_cfg.setdefault("version", None)
    cls_cfg["version"] = cls_cfg["version"] or inference.latest_version("classifier")

    detector_version = det_cfg["version"]
    classifier_version = cls_cfg["version"]
    if not detector_version or not classifier_version:
        abort(400, "no trained models found in models/ -- run train.py first")

    manual_data = load_manual_train()
    rows = []
    skipped = 0

    for image_key, entry in manual_data["gifts"].items():
        region = entry.get("region")
        if not is_valid_rect(region):
            skipped += 1
            continue

        ground_truth = _ground_truth_boxes(entry, region)

        image_path = RESOURCES_DIR / image_key
        if not image_path.exists():
            skipped += 1
            continue

        try:
            full_image = Image.open(image_path).convert("RGB")
            predictions, _timing_ms = inference.detect_region(full_image, region, "both", det_cfg, cls_cfg)
        except (ValueError, ModelNotFoundError, OSError):
            skipped += 1
            continue

        records, unclaimed = _match_predictions(ground_truth, predictions)
        rows.append(_score_image(image_key, bool(entry.get("ignore_for_training")), ground_truth, predictions, records, unclaimed))

    rows.sort(key=lambda r: r["missed"] + r["misclassified"] + r["false_positives"] + r["unclassified"], reverse=True)

    total_expected = sum(r["expected"] for r in rows)
    total_matched = sum(r["matched"] for r in rows)
    summary = {
        "images": len(rows),
        "skipped": skipped,
        "expected": total_expected,
        "matched": total_matched,
        "misclassified": sum(r["misclassified"] for r in rows),
        "unclassified": sum(r["unclassified"] for r in rows),
        "false_positives": sum(r["false_positives"] for r in rows),
        "missed": sum(r["missed"] for r in rows),
        "match_rate": round(total_matched / total_expected, 4) if total_expected else None,
    }

    return jsonify(ok=True, detector_version=detector_version,
                    classifier_version=classifier_version, rows=rows, summary=summary)


def _iou(a: dict, b: dict) -> float:
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter == 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


IOU_MATCH_THRESHOLD = 0.3

def _ground_truth_boxes(entry: dict, region: dict) -> list[dict]:
    boxes = []
    for g in entry.get("gifts") or []:
        gid = g.get("id")
        sr = g.get("subregion")
        if not gid or not is_valid_rect(sr):
            continue
        boxes.append({
            "id": gid, "name": g.get("name"),
            "x": region["x"] + sr["x"], "y": region["y"] + sr["y"], "w": sr["w"], "h": sr["h"],
        })
    return boxes

# Determine how close we were IF the underlying image has already been manually trained by a human.
# Reasonably close to the human region is good enough.
def _match_predictions(ground_truth: list[dict], predictions: list[dict]) -> tuple[list[dict], list[int]]:
    unclaimed = list(range(len(ground_truth)))
    records = []

    order = sorted(range(len(predictions)), key=lambda i: predictions[i]["detector_confidence"] or 0, reverse=True)
    for i in order:
        pred = predictions[i]
        best_iou, best_idx = 0.0, None
        for idx in unclaimed:
            iou = _iou(pred, ground_truth[idx])
            if iou > best_iou:
                best_iou, best_idx = iou, idx

        if best_idx is None or best_iou < IOU_MATCH_THRESHOLD:
            records.append({"prediction_index": i, "ground_truth_index": None, "status": "false_positive", "iou": best_iou})
            continue

        unclaimed.remove(best_idx)
        if pred["gift_id"] is None:
            status = "unclassified"
        elif pred["gift_id"] == ground_truth[best_idx]["id"]:
            status = "matched"
        else:
            status = "misclassified"
        records.append({"prediction_index": i, "ground_truth_index": best_idx, "status": status, "iou": best_iou})

    return records, unclaimed


def _score_image(image_key: str, ignore_for_training: bool, ground_truth: list[dict], predictions: list[dict], records: list[dict], unclaimed: list[int]) -> dict:
    counts = {"matched": 0, "misclassified": 0, "unclassified": 0, "false_positive": 0}
    for r in records:
        counts[r["status"]] += 1

    det_confs = [p["detector_confidence"] for p in predictions if p["detector_confidence"] is not None]
    cls_confs = [p["topk"][0]["confidence"] for p in predictions if p["topk"]]

    return {
        "image": image_key,
        "ignore_for_training": ignore_for_training,
        "expected": len(ground_truth),
        "matched": counts["matched"],
        "misclassified": counts["misclassified"],
        "unclassified": counts["unclassified"],
        "false_positives": counts["false_positive"],
        "missed": len(unclaimed),
        "avg_detector_conf": round(sum(det_confs) / len(det_confs), 3) if det_confs else None,
        "avg_classifier_conf": round(sum(cls_confs) / len(cls_confs), 3) if cls_confs else None,
    }


@tester_bp.route("/run", methods=["POST"])
def run():
    payload = request.get_json(silent=True)
    if not payload:
        abort(400, "invalid JSON body")

    image_key = payload.get("image")
    region = payload.get("region")
    mode = payload.get("mode")

    if not image_key or image_key not in find_images():
        abort(400, "unknown image")
    if not is_valid_rect(region):
        abort(400, "malformed region")

    try:
        full_image = Image.open(RESOURCES_DIR / image_key).convert("RGB")
    except OSError:
        abort(400, "could not open image")

    try:
        results, timing_ms = inference.detect_region(full_image, region, mode, payload.get("detector") or {}, payload.get("classifier") or {})
    except (ValueError, ModelNotFoundError) as e:
        abort(400, str(e))

    return jsonify(ok=True, results=results, timing_ms=timing_ms)


@tester_bp.route("/compare", methods=["POST"])
def compare():
    payload = request.get_json(silent=True) or {}
    image_key = payload.get("image")
    if not image_key or image_key not in find_images():
        abort(400, "unknown image")

    manual_data = load_manual_train()
    entry = manual_data["gifts"].get(image_key)
    region = entry.get("region") if entry else None
    if not entry or not is_valid_rect(region):
        abort(400, "no ground truth region for this image in manual_train.json")

    ground_truth = _ground_truth_boxes(entry, region)

    try:
        full_image = Image.open(RESOURCES_DIR / image_key).convert("RGB")
        predictions, timing_ms = inference.detect_region(full_image, region, "both", payload.get("detector") or {}, payload.get("classifier") or {})
    except (ValueError, ModelNotFoundError, OSError) as e:
        abort(400, str(e))

    records, missed = _match_predictions(ground_truth, predictions)
    score = _score_image(image_key, bool(entry.get("ignore_for_training")), ground_truth, predictions, records, missed)

    return jsonify(
        ok=True, region=region,
        ground_truth=ground_truth, predictions=predictions,
        matches=records, missed_indices=missed, score=score, timing_ms=timing_ms,
    )
