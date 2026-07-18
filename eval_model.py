import argparse
import json
from pathlib import Path

from PIL import Image

import inference
from inference import ModelNotFoundError
from manual_trainer import RESOURCES_DIR, load_manual_train
from tester import _ground_truth_boxes, _match_predictions, _score_image

def decompose(rows: list[dict]) -> dict:
    expected = sum(r["expected"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    misclassified = sum(r["misclassified"] for r in rows)
    unclassified = sum(r["unclassified"] for r in rows)
    missed = sum(r["missed"] for r in rows)
    false_positives = sum(r["false_positives"] for r in rows)
    detected = matched + misclassified + unclassified
    return {
        "images": len(rows),
        "expected": expected,
        "detector_recall": round(detected / expected, 4) if expected else None,
        "classifier_acc_given_detected": round(matched / detected, 4) if detected else None,
        "match_rate": round(matched / expected, 4) if expected else None,
        "matched": matched,
        "misclassified": misclassified,
        "unclassified": unclassified,
        "missed": missed,
        "false_positives": false_positives,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--detector", required=True, help="models/<version> for the detector")
    parser.add_argument("--classifier", required=True, help="models/<version> for the classifier")
    parser.add_argument("--conf", type=float, default=0.45)
    parser.add_argument("--iou", type=float, default=0.1)
    parser.add_argument("--imgsz", type=int, default=384)
    parser.add_argument("--cls-topk", type=int, default=3)
    parser.add_argument("--cls-conf", type=float, default=0.6)
    parser.add_argument("--worst", type=int, default=10, help="print the N worst-scoring held-out images")
    parser.add_argument("--confusion", type=int, default=0, help="print the top-N (true -> predicted) confusion pairs from the held-out set, including near-miss unclassified boxes")
    parser.add_argument("--json", type=Path, default=None, help="also write full per-image rows to this path")
    args = parser.parse_args()

    det_cfg = {"version": args.detector, "conf": args.conf, "iou": args.iou, "imgsz": args.imgsz}
    cls_cfg = {"version": args.classifier, "topk": args.cls_topk, "conf": args.cls_conf}

    manual_data = load_manual_train()
    rows = []
    skipped = 0
    confusion_pairs: dict[tuple[str, str], int] = {}

    for image_key, entry in manual_data["gifts"].items():
        region = entry.get("region")
        if not region:
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
        row = _score_image(image_key, bool(entry.get("ignore_for_training")), ground_truth, predictions, records, unclaimed)
        rows.append(row)

        if args.confusion and entry.get("ignore_for_training"):
            for r in records:
                if r["status"] not in ("misclassified", "unclassified") or r["ground_truth_index"] is None:
                    continue
                true_name = ground_truth[r["ground_truth_index"]]["name"]
                pred = predictions[r["prediction_index"]]
                top1 = pred["topk"][0] if pred["topk"] else None
                pred_name = f"{top1['name']} ({top1['confidence']:.2f})" if top1 else "?"
                if r["status"] == "unclassified":
                    pred_name += " [below conf cutoff]"
                key = (true_name, pred_name)
                confusion_pairs[key] = confusion_pairs.get(key, 0) + 1

    held_out = [r for r in rows if r["ignore_for_training"]]
    full = rows

    print(f"detector={args.detector} classifier={args.classifier} "
          f"conf={args.conf} iou={args.iou} imgsz={args.imgsz} cls_topk={args.cls_topk} cls_conf={args.cls_conf}")
    print(f"scored {len(rows)} images ({skipped} skipped)\n")

    for label, subset in (("HELD-OUT (verify/, never trained on)", held_out), ("FULL (includes trained-on images)", full)):
        d = decompose(subset)
        print(f"-- {label} --")
        print(f"  images={d['images']} expected_boxes={d['expected']}")
        print(f"  detector_recall={d['detector_recall']}  classifier_acc_given_detected={d['classifier_acc_given_detected']}  match_rate={d['match_rate']}")
        print(f"  matched={d['matched']} misclassified={d['misclassified']} unclassified={d['unclassified']} missed={d['missed']} false_positives={d['false_positives']}")
        print()

    worst = sorted(held_out, key=lambda r: r["missed"] + r["misclassified"] + r["false_positives"] + r["unclassified"], reverse=True)[:args.worst]
    if worst:
        print(f"-- worst {len(worst)} held-out images --")
        for r in worst:
            print(f"  {r['image']}: expected={r['expected']} matched={r['matched']} misclassified={r['misclassified']} "
                  f"unclassified={r['unclassified']} missed={r['missed']} false_positives={r['false_positives']}")

    if args.confusion:
        top_pairs = sorted(confusion_pairs.items(), key=lambda kv: kv[1], reverse=True)[:args.confusion]
        print(f"\n-- top {len(top_pairs)} held-out confusion pairs (true -> predicted) --")
        for (true_name, pred_name), count in top_pairs:
            print(f"  {count}x  {true_name!r} -> {pred_name}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n")
        print(f"\nwrote per-image rows to {args.json}")


if __name__ == "__main__":
    main()
