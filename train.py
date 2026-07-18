# - detector: YOLO nano (ultralytics), single class ("gift"), localization only
# - classifier: MobileNetV3-Small (torchvision), fine-tuned to the full set of gift ids

import argparse
import json
import random
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

import compose_synthetic
from constants import CLASSIFIER_INPUT_SIZE, LETTERBOX_GRAY_FILL

BASE_DIR = Path(__file__).parent
MANUAL_JSON = BASE_DIR / "resources" / "manual_train.json"
MANUAL_ROOT = BASE_DIR / "resources"
SYNTH_ROOT = BASE_DIR / "data" / "synth"
SYNTH_JSON = SYNTH_ROOT / "synth_train.json"
MODELS_DIR = BASE_DIR / "models"

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

class Example:
    def __init__(self, key: str, image_path: Path, boxes: list[dict], region: dict):
        self.key = key
        self.image_path = image_path
        self.boxes = boxes
        self.region = region


class UnidentifiedBoxError(Exception):
    """"""

def iter_examples(json_path: Path, image_root: Path, tag: str) -> list[Example]:
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text())
    examples = []
    skipped = 0
    unidentified = []
    for key, entry in data.get("gifts", {}).items():
        if entry.get("ignore_for_training"):
            continue
        image_path = image_root / key
        if not image_path.exists():
            skipped += 1
            continue
        region = entry.get("region") or {"x": 0, "y": 0}
        boxes = []
        for gift in entry.get("gifts") or []:
            if not gift.get("id"):
                unidentified.append(f"  {tag}:{key} -> {gift.get('name')!r} has no id")
                continue
            sr = gift["subregion"]
            boxes.append({
                "id": gift["id"],
                "name": gift["name"],
                "x": region.get("x", 0) + sr["x"],
                "y": region.get("y", 0) + sr["y"],
                "w": sr["w"],
                "h": sr["h"],
            })
        examples.append(Example(f"{tag}:{key}", image_path, boxes, region))
    if unidentified:
        raise UnidentifiedBoxError(f"{len(unidentified)} labeled region(s) in {json_path} have no gift id")
    if skipped:
        print(f"  {tag}: skipped {skipped} entries with missing image files")
    return examples


def load_merged_examples() -> list[Example]:
    manual = iter_examples(MANUAL_JSON, MANUAL_ROOT, "manual")
    synth = iter_examples(SYNTH_JSON, SYNTH_ROOT, "synth")
    print(f"Merged dataset: {len(manual)} manual images, {len(synth)} synthetic images")
    return manual + synth

def safe_filename(key: str) -> str:
    return key.replace("/", "__").replace(":", "_").replace(" ", "_")


def build_detector_dataset(examples: list[Example], tmp_dir: Path, val_frac: float, seed: int) -> Path:
    root = tmp_dir / "detector"
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    shuffled = examples[:]
    rng.shuffle(shuffled)
    n_val = max(1, round(len(shuffled) * val_frac)) if len(shuffled) > 1 else 0
    val_keys = {ex.key for ex in shuffled[:n_val]}

    counts = {"train": 0, "val": 0}
    box_counts = {"train": 0, "val": 0}
    negative_counts = {"train": 0, "val": 0}
    for ex in examples:
        split = "val" if ex.key in val_keys else "train"
        try:
            with Image.open(ex.image_path) as im:
                im = im.convert("RGB")
                full_w, full_h = im.size

                rx0 = max(0, round(ex.region.get("x", 0)))
                ry0 = max(0, round(ex.region.get("y", 0)))
                rw = ex.region.get("w", full_w - rx0)
                rh = ex.region.get("h", full_h - ry0)
                rx1 = min(full_w, rx0 + round(rw))
                ry1 = min(full_h, ry0 + round(rh))
                if rx1 <= rx0 or ry1 <= ry0:
                    print(f"{ex.image_path}: empty")
                    continue

                crop = im.crop((rx0, ry0, rx1, ry1))
                width, height = crop.size
                stem = Path(safe_filename(ex.key)).stem
                out_name = f"{stem}.jpg"
                crop.save(root / "images" / split / out_name, quality=95)
        except OSError as e:
            print(f"skip {ex.image_path}: {e}")
            continue

        lines = []
        for box in ex.boxes:
            x0, y0 = max(0, box["x"] - rx0), max(0, box["y"] - ry0)
            x1, y1 = min(width, box["x"] + box["w"] - rx0), min(height, box["y"] + box["h"] - ry0)
            if x1 <= x0 or y1 <= y0:
                continue
            cx, cy = (x0 + x1) / 2 / width, (y0 + y1) / 2 / height
            bw, bh = (x1 - x0) / width, (y1 - y0) / height
            lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        (root / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        counts[split] += 1
        box_counts[split] += len(lines)
        if not lines:
            negative_counts[split] += 1

    data_yaml = root / "data.yaml"
    data_yaml.write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\nnames:\n  0: gift\n"
    )
    print(f"Detector dataset: train={counts['train']} images/{box_counts['train']} boxes "
          f"({negative_counts['train']} negative), "
          f"val={counts['val']} images/{box_counts['val']} boxes ({negative_counts['val']} negative)")
    return data_yaml

def build_classifier_dataset(examples: list[Example], tmp_dir: Path, val_frac: float, seed: int) -> tuple[list[tuple[Path, int, str]], list[tuple[Path, int, str]], list[dict]]:
    root = tmp_dir / "classifier"
    root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)

    by_id: dict[str, list[tuple[Path, dict, str]]] = {}
    names_by_id: dict[str, str] = {}
    for ex in examples:
        source = "manual" if ex.key.startswith("manual:") else "synth"
        for box in ex.boxes:
            by_id.setdefault(box["id"], []).append((ex.image_path, box, source))
            names_by_id[box["id"]] = box["name"]

    class_ids = sorted(by_id)
    class_map = [{"index": i, "id": gid, "name": names_by_id[gid]} for i, gid in enumerate(class_ids)]
    id_to_index = {gid: i for i, gid in enumerate(class_ids)}

    train_samples: list[tuple[Path, int, str]] = []
    val_samples: list[tuple[Path, int, str]] = []
    image_cache: dict[Path, Image.Image] = {}

    for gid, instances in by_id.items():
        rng.shuffle(instances)
        n_val = 1 if len(instances) >= 4 else 0
        n_val = min(n_val, max(0, len(instances) - 1))

        (root / "train" / gid).mkdir(parents=True, exist_ok=True)
        if n_val:
            (root / "val" / gid).mkdir(parents=True, exist_ok=True)

        for i, (image_path, box, source) in enumerate(instances):
            split = "val" if i < n_val else "train"
            im = image_cache.get(image_path)
            if im is None:
                try:
                    im = Image.open(image_path).convert("RGB")
                except OSError as e:
                    print(f"  skip {image_path}: {e}")
                    continue
                image_cache[image_path] = im

            x0, y0 = max(0, box["x"]), max(0, box["y"])
            x1, y1 = min(im.width, box["x"] + box["w"]), min(im.height, box["y"] + box["h"])
            if x1 <= x0 or y1 <= y0:
                continue
            crop = im.crop((x0, y0, x1, y1))

            out_path = root / split / gid / f"{gid}_{i:04d}.jpg"
            crop.save(out_path, quality=95)

            label = id_to_index[gid]
            (train_samples if split == "train" else val_samples).append((out_path, label, source))

    n_manual = sum(1 for _, _, s in train_samples if s == "manual")
    print(f"Classifier dataset: {len(class_ids)} classes, "
          f"train={len(train_samples)} crops ({n_manual} manual, {len(train_samples) - n_manual} synth), val={len(val_samples)} crops")
    return train_samples, val_samples, class_map


# Split synthetic vs manual training priority (for now)
# This seems to be a good enough split to prevent over prioritizing synthetic data
#
# Note, this sorta conflicts with trouble_rating trying to MAKE more synthetic data for specific problem gifts,
# so this might need to be tweaked later to allow a high trouble rating to affect the synthetic/manual ratio
MANUAL_SYNTHETIC_RATIO = 0.5
def make_sample_weights(train_samples: list[tuple[Path, int, str]], ratio: float = MANUAL_SYNTHETIC_RATIO) -> list[float]:
    class_totals: dict[int, int] = {}
    source_counts: dict[tuple[int, str], int] = {}
    for _, label, source in train_samples:
        class_totals[label] = class_totals.get(label, 0) + 1
        source_counts[(label, source)] = source_counts.get((label, source), 0) + 1

    weights = []
    for _, label, source in train_samples:
        sources_present = sum(1 for s in ("manual", "synth") if source_counts.get((label, s), 0) > 0)
        if sources_present == 2:
            source_frac = ratio if source == "manual" else (1 - ratio)
            share = class_totals[label] * source_frac
        else:
            share = class_totals[label]
        weights.append(share / source_counts[(label, source)])
    return weights

class CropDataset(Dataset):
    def __init__(self, samples: list[tuple[Path, int]], transform):
        self.samples = samples
        self.transform = transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]
        with Image.open(path) as im:
            img = im.convert("RGB")
        return self.transform(img), label

def next_version_dir() -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    existing = [int(p.name[1:]) for p in MODELS_DIR.glob("v*") if p.name[1:].isdigit()]
    version_dir = MODELS_DIR / f"v{max(existing, default=0) + 1}"
    version_dir.mkdir(parents=True)
    return version_dir

def train_detector(data_yaml: Path, epochs: int, imgsz: int, batch: int, run_root: Path, version_dir: Path) -> dict:
    from ultralytics import YOLO

    try:
        model = YOLO("yolo11n.pt")
        print("Detector: starting from pretrained yolo11n.pt")
    except Exception as e:
        print(f"Detector: pretrained weights unavailable ({e}), starting from scratch")
        model = YOLO("yolo11n.yaml")

    results = model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=DEVICE,
        project=str(run_root / "runs"),
        name="detector",
        exist_ok=True,
        verbose=False,
    )
    run_dir = Path(results.save_dir)
    best_pt = run_dir / "weights" / "best.pt"

    onnx_path = model.export(format="onnx", imgsz=imgsz)

    shutil.copy2(best_pt, version_dir / "detector.pt")
    shutil.copy2(onnx_path, version_dir / "detector.onnx")
    print(f"Detector: saved {version_dir / 'detector.pt'} and {version_dir / 'detector.onnx'}")
    print(f"Detector: full training run at {run_dir}")

    try:
        metrics = {k: float(v) for k, v in results.results_dict.items()}
    except Exception:
        metrics = {}
    metrics["imgsz"] = imgsz
    return metrics

def train_classifier(
    train_samples: list[tuple[Path, int, str]],
    val_samples: list[tuple[Path, int, str]],
    class_map: list[dict],
    epochs: int,
    batch_size: int,
    version_dir: Path,
) -> dict:
    device = torch.device(DEVICE)
    num_classes = len(class_map)

    train_tf = transforms.Compose([
        transforms.Resize((CLASSIFIER_INPUT_SIZE, CLASSIFIER_INPUT_SIZE)),
        transforms.RandomAffine(degrees=0, translate=(0.06, 0.06), scale=(0.88, 1.12), fill=LETTERBOX_GRAY_FILL),
        transforms.ColorJitter(brightness=0.3, contrast=0.3),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((CLASSIFIER_INPUT_SIZE, CLASSIFIER_INPUT_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])

    train_pairs = [(path, label) for path, label, _ in train_samples]
    val_pairs = [(path, label) for path, label, _ in val_samples]

    sampler = WeightedRandomSampler(make_sample_weights(train_samples), num_samples=len(train_pairs), replacement=True)
    train_loader = DataLoader(CropDataset(train_pairs, train_tf), batch_size=batch_size, sampler=sampler, num_workers=0)
    val_loader = DataLoader(CropDataset(val_pairs, eval_tf), batch_size=batch_size, shuffle=False, num_workers=0) if val_pairs else None

    model = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.DEFAULT)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, num_classes)
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.CrossEntropyLoss()

    best_state = None
    best_acc = -1.0

    for epoch in range(1, epochs + 1):
        model.train()
        running_loss = 0.0
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * images.size(0)
        scheduler.step()
        train_loss = running_loss / max(1, len(train_loader.dataset))

        acc_str = "n/a"
        if val_loader is not None:
            model.eval()
            correct, total = 0, 0
            with torch.no_grad():
                for images, labels in val_loader:
                    images, labels = images.to(device), labels.to(device)
                    preds = model(images).argmax(dim=1)
                    correct += (preds == labels).sum().item()
                    total += labels.size(0)
            acc = correct / max(1, total)
            acc_str = f"{acc:.3f}"
            if acc >= best_acc:
                best_acc = acc
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        print(f"Classifier epoch {epoch}/{epochs}: train_loss={train_loss:.4f} val_acc={acc_str}")

    model.load_state_dict(best_state)
    model.to("cpu")
    model.eval()

    torch.save(model.state_dict(), version_dir / "classifier.pt")
    (version_dir / "classifier_classes.json").write_text(json.dumps(class_map, indent=2, ensure_ascii=False) + "\n")

    dummy = torch.randn(1, 3, CLASSIFIER_INPUT_SIZE, CLASSIFIER_INPUT_SIZE)
    torch.onnx.export(
        model, dummy, version_dir / "classifier.onnx",
        input_names=["input"], output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    print(f"Classifier: saved {version_dir / 'classifier.pt'}, "
          f"{version_dir / 'classifier.onnx'}, {version_dir / 'classifier_classes.json'}")

    return {"val_acc": best_acc if best_acc >= 0 else None, "num_classes": num_classes}

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=24, help="synthetic icon repeats, see compose_synthetic.py")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip-synth", action="store_true", help="reuse existing data/synth/synth_train.json instead of regenerating")
    parser.add_argument("--val-frac", type=float, default=0.1)
    parser.add_argument("--detector-epochs", type=int, default=30)
    parser.add_argument("--detector-imgsz", type=int, default=384)
    parser.add_argument("--detector-batch", type=int, default=64)
    parser.add_argument("--classifier-epochs", type=int, default=16)
    parser.add_argument("--classifier-batch", type=int, default=128)
    parser.add_argument("--keep-tmp", action="store_true", help="don't delete the merged temp dataset afterwards")
    args = parser.parse_args()

    print(f"Device: {DEVICE}" + (f" ({torch.cuda.get_device_name(0)})" if DEVICE == "cuda" else ""))

    if not args.skip_synth:
        print("=== Regenerating synthetic dataset ===")
        compose_synthetic.generate(repeats=args.repeats, seed=args.seed, out=SYNTH_ROOT)
    else:
        print("=== Skipping synthetic generation (--skip-synth) ===")

    print("\n=== Merging manual + synthetic datasets ===")
    examples = load_merged_examples()
    if not examples:
        raise SystemExit("no training examples found")

    version_dir = next_version_dir()
    print(f"\n=== Training run {version_dir.name} ===")

    tmp_dir = Path(tempfile.mkdtemp(prefix="egogift_train_"))
    print(f"Building temp merged dataset at {tmp_dir}")
    try:
        print("\n=== Building detector dataset ===")
        data_yaml = build_detector_dataset(examples, tmp_dir, args.val_frac, args.seed)

        print("\n=== Building classifier dataset ===")
        train_samples, val_samples, class_map = build_classifier_dataset(examples, tmp_dir, args.val_frac, args.seed)

        print("\n=== Training detector (YOLO nano) ===")
        detector_metrics = train_detector(data_yaml, args.detector_epochs, args.detector_imgsz, args.detector_batch, tmp_dir, version_dir)

        print("\n=== Training classifier (MobileNetV3-Small) ===")
        classifier_metrics = train_classifier(train_samples, val_samples, class_map, args.classifier_epochs, args.classifier_batch, version_dir)
    finally:
        if args.keep_tmp:
            print(f"\n--keep-tmp: leaving merged dataset at {tmp_dir}")
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    metadata = {
        "version": version_dir.name,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "args": vars(args),
        "dataset": {
            "manual_images": sum(1 for ex in examples if ex.key.startswith("manual:")),
            "synthetic_images": sum(1 for ex in examples if ex.key.startswith("synth:")),
        },
        "detector_metrics": detector_metrics,
        "classifier_metrics": classifier_metrics,
    }
    (version_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    print(f"\nDone. Artifacts in {version_dir}")


if __name__ == "__main__":
    main()
