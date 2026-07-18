"""
Script to spit out a bunch of fake ego gift images for training. Overlays vector art over various backgrounds.

Realistically this isn't the best approach to ensuring accuracy, but it is a nice touch to prevent overfitting e.g.
we fail to detect on weird UIs that limbus might have in the future.

see data/synth after running to see what it spits out
"""
import argparse
import itertools
import json
import random
import sqlite3
from pathlib import Path

from PIL import Image, ImageEnhance

from migrate import DB_PATH

BASE_DIR = Path(__file__).parent
BG_DIR = BASE_DIR / "resources" / "bg"
DEFAULT_OUT_DIR = BASE_DIR / "data" / "synth"

SIZE_BANDS = {"large": (70, 90), "small": (40, 55)}
SIZE_JITTER = (0.9, 1.1)
CONTRAST_JITTER = (0.7, 1.3)
BRIGHTNESS_JITTER = (-30, 10)
DISABLED_PROBABILITY = 0.175
DISABLED_FACTOR = (0.35, 0.65)
PADDING_JITTER = (0.0, 0.03)
MARGIN = 6
JPEG_QUALITY = 92


def load_gifts() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, name, image_path, trouble_rating FROM ego_gifts WHERE image_path IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    gifts = []
    for gift_id, name, image_path, trouble_rating in rows:
        full_path = BASE_DIR / image_path
        if not full_path.exists():
            print(f"skip {gift_id}: {full_path} does not exist")
            continue
        gifts.append({"id": gift_id, "name": name, "path": full_path, "trouble_rating": trouble_rating})
    return gifts


def load_content(path: Path) -> Image.Image:
    """Loads an icon and crops it to its non-transparent bounding box, so
    the padding baked into the source canvas doesn't get treated as part
    of the icon."""
    im = Image.open(path).convert("RGBA")
    alpha = im.split()[-1]
    bbox = alpha.getbbox()
    if bbox is None:
        return im
    return im.crop(bbox)


def resize_to_band(content: Image.Image, target: int) -> Image.Image:
    w, h = content.size
    scale = target / max(w, h)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return content.resize(new_size, Image.LANCZOS)


def jitter_icon_appearance(content: Image.Image, rng: random.Random) -> Image.Image:
    contrast = rng.uniform(*CONTRAST_JITTER)
    brightness = rng.uniform(*BRIGHTNESS_JITTER)
    lut = [max(0, min(255, round(i * contrast + brightness))) for i in range(256)]

    r, g, b, a = content.split()
    r, g, b = r.point(lut), g.point(lut), b.point(lut)

    if rng.random() < DISABLED_PROBABILITY:
        factor = rng.uniform(*DISABLED_FACTOR)
        dark_lut = [max(0, min(255, round(i * factor))) for i in range(256)]
        r, g, b = r.point(dark_lut), g.point(dark_lut), b.point(dark_lut)

    return Image.merge("RGBA", (r, g, b, a))

def grid_area(bg_size: tuple[int, int], padding_frac: float) -> tuple[int, int, int, int]:
    bg_w, bg_h = bg_size
    pad_x, pad_y = round(bg_w * padding_frac), round(bg_h * padding_frac)
    return pad_x, pad_y, max(1, bg_w - 2 * pad_x), max(1, bg_h - 2 * pad_y)

def grid_capacity(area_size: tuple[int, int], icon_size: int) -> tuple[int, int]:
    area_w, area_h = area_size
    cell = icon_size + MARGIN
    cols = max(1, (area_w - MARGIN) // cell)
    rows = max(1, (area_h - MARGIN) // cell)
    return cols, rows

def render_scene(
    bg_path: Path, icons: list[dict], icon_size: int, rng: random.Random,
    grid_x0: int, grid_y0: int, cols: int, rows: int,
) -> tuple[Image.Image, list[dict]]:
    bg = Image.open(bg_path).convert("RGBA")
    cell = icon_size + MARGIN

    cell_indices = rng.sample(range(cols * rows), len(icons))

    labels = []
    for gift, cell_idx in zip(icons, cell_indices):
        content = load_content(gift["path"])
        jittered_size = max(8, round(icon_size * rng.uniform(*SIZE_JITTER)))
        content = resize_to_band(content, jittered_size)
        content = jitter_icon_appearance(content, rng)

        row, col = divmod(cell_idx, cols)
        cell_x = grid_x0 + MARGIN + col * cell
        cell_y = grid_y0 + MARGIN + row * cell
        offset_x = (icon_size - content.width) // 2
        offset_y = (icon_size - content.height) // 2
        paste_x = cell_x + offset_x
        paste_y = cell_y + offset_y

        bg.paste(content, (paste_x, paste_y), content)
        labels.append({
            "id": gift["id"],
            "name": gift["name"],
            "subregion": {
                "x": paste_x, "y": paste_y,
                "w": content.width, "h": content.height,
            },
        })

    return bg.convert("RGB"), labels


def build_queue(gifts: list[dict], repeats: int, rng: random.Random) -> list[dict]:
    queue = list(itertools.chain.from_iterable(
        [gift] * max(1, round(repeats * gift["trouble_rating"])) for gift in gifts
    ))
    rng.shuffle(queue)
    return queue


def jitter_background(bg: Image.Image, rng: random.Random) -> Image.Image:
    bg = ImageEnhance.Contrast(bg).enhance(0.85 + rng.random() * 0.3)
    bg = ImageEnhance.Brightness(bg).enhance(0.85 + rng.random() * 0.3)
    return bg


def generate(repeats: int = 6, seed: int = 0, out: Path = DEFAULT_OUT_DIR, negative_fraction: float = 0.1) -> Path:
    rng = random.Random(seed)

    gifts = load_gifts()
    print(f"Loaded {len(gifts)} gifts")

    bg_paths = sorted(BG_DIR.glob("*.png"))
    if not bg_paths:
        raise SystemExit(f"no backgrounds found in {BG_DIR}")
    bg_cycle = itertools.cycle(bg_paths)

    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    labels: dict[str, dict] = {}
    scene_counter = 0
    per_bg_counts: dict[str, int] = {p.name: 0 for p in bg_paths}

    for size_band in SIZE_BANDS:
        queue = build_queue(gifts, repeats, rng)
        size_lo, size_hi = SIZE_BANDS[size_band]

        while queue:
            bg_path = next(bg_cycle)
            icon_size = rng.randint(size_lo, size_hi)
            padding_frac = rng.uniform(*PADDING_JITTER)
            grid_x0, grid_y0, usable_w, usable_h = grid_area(Image.open(bg_path).size, padding_frac)
            cols, rows = grid_capacity((usable_w, usable_h), icon_size)
            capacity = cols * rows
            fill_count = rng.randint(1, min(capacity, len(queue)))

            batch, queue = queue[:fill_count], queue[fill_count:]
            scene, scene_labels = render_scene(bg_path, batch, icon_size, rng, grid_x0, grid_y0, cols, rows)

            scene_counter += 1
            filename = f"scene_{scene_counter:05d}.jpg"
            scene.save(images_dir / filename, quality=JPEG_QUALITY)
            per_bg_counts[bg_path.name] += 1

            labels[f"images/{filename}"] = {
                "region": {"x": 0, "y": 0, "w": scene.width, "h": scene.height},
                "gifts": scene_labels,
                "ignore_for_training": False,
                "source": "synthetic",
                "meta": {"bg": bg_path.name, "size_band": size_band, "icon_px": icon_size, "padding_frac": round(padding_frac, 3)},
            }

    positive_scenes = scene_counter
    negative_target = round(positive_scenes * negative_fraction)
    for _ in range(negative_target):
        bg_path = next(bg_cycle)
        bg = jitter_background(Image.open(bg_path).convert("RGB"), rng)

        scene_counter += 1
        filename = f"scene_{scene_counter:05d}.jpg"
        bg.save(images_dir / filename, quality=JPEG_QUALITY)
        per_bg_counts[bg_path.name] += 1

        labels[f"images/{filename}"] = {
            "region": {"x": 0, "y": 0, "w": bg.width, "h": bg.height},
            "gifts": [],
            "ignore_for_training": False,
            "source": "synthetic",
            "meta": {"bg": bg_path.name, "negative": True},
        }

    labels_path = out / "synth_train.json"
    labels_path.write_text(json.dumps({"gifts": labels}, indent=2, ensure_ascii=False) + "\n")

    total_instances = sum(len(v["gifts"]) for v in labels.values())
    print(f"Generated {scene_counter} scenes ({negative_target} negative), {total_instances} icon instances")
    for name, count in per_bg_counts.items():
        print(f"  {name}: {count} scenes")
    print(f"Wrote {labels_path}")
    return labels_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=6, help="times each icon appears per size band")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--negative-fraction", type=float, default=0.1, help="icon-free background scenes, as a fraction of the positive scene count")
    args = parser.parse_args()

    generate(repeats=args.repeats, seed=args.seed, out=args.out, negative_fraction=args.negative_fraction)

if __name__ == "__main__":
    main()
