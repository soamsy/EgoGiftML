import json
import time

import cv2
import numpy as np
from PIL import Image

from constants import CLASSIFIER_INPUT_SIZE, MODELS_DIR, LETTERBOX_GRAY

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

class ModelNotFoundError(Exception):
    """"""

# Finds any models with a .pt or .onnx extension under models/
def list_versions(kind: str) -> list[dict]:
    if not MODELS_DIR.exists():
        return []
    versions = []
    for d in MODELS_DIR.iterdir():
        if not d.is_dir() or (not (d / f"{kind}.pt").exists() or not (d / f"{kind}.onnx").exists()):
            continue
        meta = {}
        meta_path = d / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        versions.append({"version": d.name, "metadata": meta, "mtime": d.stat().st_mtime})
    versions.sort(key=lambda v: v["mtime"], reverse=True)
    return versions

def _version_number(name: str) -> int | None:
    return int(name[1:]) if name.startswith("v") and name[1:].isdigit() else None

def latest_version(kind: str) -> str | None:
    versions = list_versions(kind)
    numbered = [v for v in versions if _version_number(v["version"]) is not None]
    if numbered:
        return max(numbered, key=lambda v: _version_number(v["version"]))["version"]
    return versions[0]["version"] if versions else None

_detector_cache: dict[str, cv2.dnn.Net] = {}
_classifier_cache: dict[str, tuple] = {}

def get_detector(version: str) -> cv2.dnn.Net:
    if version not in _detector_cache:
        onnx_path = MODELS_DIR / version / "detector.onnx"
        if not onnx_path.exists():
            raise ModelNotFoundError(f"Unknown detector version {version!r} (no detector.onnx)")
        _detector_cache[version] = cv2.dnn.readNetFromONNX(str(onnx_path))
    return _detector_cache[version]


def get_classifier(version: str) -> tuple:
    if version not in _classifier_cache:
        onnx_path = MODELS_DIR / version / "classifier.onnx"
        classes_path = MODELS_DIR / version / "classifier_classes.json"
        if not onnx_path.exists() or not classes_path.exists():
            raise ModelNotFoundError(f"Unknown classifier version {version!r} (no classifier.onnx)")
        class_map = json.loads(classes_path.read_text())
        _classifier_cache[version] = (cv2.dnn.readNetFromONNX(str(onnx_path)), class_map)
    return _classifier_cache[version]

# Utility to pad a non-square image with letterbox strips that are gray
# This needs to match the gray used in training to denote "nothing space",
# hence the use of LETTERBOX_GRAY
def _letterbox(img: np.ndarray, new_size: int, color=LETTERBOX_GRAY) -> tuple[np.ndarray, float, tuple[int, int]]:
    h, w = img.shape[:2]
    r = min(new_size / h, new_size / w)
    new_unpad = (round(w * r), round(h * r))
    dw, dh = (new_size - new_unpad[0]) / 2, (new_size - new_unpad[1]) / 2

    resized = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return padded, r, (left, top)

def _xywh2xyxy(boxes: np.ndarray) -> np.ndarray:
    xyxy = np.empty_like(boxes)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return xyxy

def run_detector(net: cv2.dnn.Net, img_bgr: np.ndarray, conf: float, iou: float, imgsz: int) -> list[dict]:
    padded, r, (pad_left, pad_top) = _letterbox(img_bgr, imgsz)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...])

    net.setInput(blob)
    raw = net.forward()

    preds = raw[0].T
    scores = preds[:, 4]
    keep = scores > conf
    preds, scores = preds[keep], scores[keep]
    if len(preds) == 0:
        return []

    boxes_xyxy = _xywh2xyxy(preds[:, :4])
    nms_input = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes_xyxy]
    idxs = np.array(cv2.dnn.NMSBoxes(nms_input, scores.tolist(), conf, iou)).flatten()
    if len(idxs) == 0:
        return []

    img_h, img_w = img_bgr.shape[:2]
    results = []
    for i in idxs:
        x0, y0, x1, y1 = (float(v) for v in boxes_xyxy[i])
        x0, x1 = (x0 - pad_left) / r, (x1 - pad_left) / r
        y0, y1 = (y0 - pad_top) / r, (y1 - pad_top) / r
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(float(img_w), x1), min(float(img_h), y1)
        if x1 <= x0 or y1 <= y0:
            continue
        results.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "detector_confidence": float(scores[i])})
    return results

def run_classifier(version: str, crop_bgr: np.ndarray, topk: int) -> list[dict]:
    net, class_map = get_classifier(version)
    if crop_bgr.size == 0:
        return []

    resized = cv2.resize(crop_bgr, (CLASSIFIER_INPUT_SIZE, CLASSIFIER_INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rgb = (rgb - IMAGENET_MEAN) / IMAGENET_STD
    blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...].astype(np.float32))

    net.setInput(blob)
    logits = net.forward()[0]
    exp = np.exp(logits - logits.max())
    probs = exp / exp.sum()

    k = min(topk, probs.shape[0])
    top_idx = np.argsort(probs)[::-1][:k]
    return [{"id": class_map[i]["id"], "name": class_map[i]["name"], "confidence": float(probs[i])} for i in top_idx]

def _to_bgr(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

def detect_region(full_image: Image.Image, region: dict, mode: str, det_cfg: dict, cls_cfg: dict) -> tuple[list[dict], dict]:
    if mode not in ("detector", "classifier", "both"):
        raise ValueError("mode must be detector, classifier, or both")

    full_bgr = _to_bgr(full_image)
    img_h, img_w = full_bgr.shape[:2]

    x0, y0 = max(0, round(region["x"])), max(0, round(region["y"]))
    x1 = min(img_w, round(region["x"] + region["w"]))
    y1 = min(img_h, round(region["y"] + region["h"]))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("region is empty")
    region_crop = full_bgr[y0:y1, x0:x1]

    topk = max(1, int(cls_cfg.get("topk", 3)))
    min_conf = float(cls_cfg.get("conf", 0.0))
    results: list[dict] = []
    timing_ms: dict[str, float] = {}

    if mode in ("detector", "both"):
        detector_version = det_cfg.get("version")
        if not detector_version:
            raise ValueError("detector version required")
        net = get_detector(detector_version)

        t0 = time.perf_counter()
        boxes = run_detector(
            net, region_crop,
            conf=float(det_cfg.get("conf", 0.25)),
            iou=float(det_cfg.get("iou", 0.45)),
            imgsz=int(det_cfg.get("imgsz", 384)),
        )
        timing_ms["detector"] = round((time.perf_counter() - t0) * 1000, 1)

        for box in boxes:
            results.append({
                "x": x0 + box["x"], "y": y0 + box["y"], "w": box["w"], "h": box["h"],
                "detector_confidence": box["detector_confidence"],
                "gift_id": None, "gift_name": None, "classifier_confidence": None, "topk": [],
            })

        if mode == "both" and results:
            classifier_version = cls_cfg.get("version")
            if not classifier_version:
                raise ValueError("classifier version required")

            t0 = time.perf_counter()
            for row in results:
                bx, by, bw, bh = round(row["x"]), round(row["y"]), round(row["w"]), round(row["h"])
                sub_crop = full_bgr[by:by + bh, bx:bx + bw]
                preds = run_classifier(classifier_version, sub_crop, topk)
                row["topk"] = preds
                if preds and preds[0]["confidence"] >= min_conf:
                    row["gift_id"] = preds[0]["id"]
                    row["gift_name"] = preds[0]["name"]
                    row["classifier_confidence"] = preds[0]["confidence"]
            timing_ms["classifier"] = round((time.perf_counter() - t0) * 1000, 1)

    elif mode == "classifier":
        classifier_version = cls_cfg.get("version")
        if not classifier_version:
            raise ValueError("classifier version required")

        t0 = time.perf_counter()
        preds = run_classifier(classifier_version, region_crop, topk)
        timing_ms["classifier"] = round((time.perf_counter() - t0) * 1000, 1)
        accepted = bool(preds) and preds[0]["confidence"] >= min_conf

        results.append({
            "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "detector_confidence": None,
            "gift_id": preds[0]["id"] if accepted else None,
            "gift_name": preds[0]["name"] if accepted else None,
            "classifier_confidence": preds[0]["confidence"] if accepted else None,
            "topk": preds,
        })

    return results, timing_ms