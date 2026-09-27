#!/usr/bin/env python3
"""Build draw.io SVG libraries from the AWS icon package ZIP."""

import argparse
import base64
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET


FAMILIES = {
    "Architecture-Service-Icons": ("AWS-Architecture-Services", "service"),
    "Resource-Icons": ("AWS-Resource-Icons", "resource"),
    "Category-Icons": ("AWS-Category-Icons", "category"),
    "Architecture-Group-Icons": ("AWS-Architecture-Groups", "group"),
}
SIZE_SUFFIX = re.compile(r"_(16|32|48|64)(?:_(Light|Dark))?$", re.IGNORECASE)
NUMERIC_LENGTH = re.compile(r"^\s*(\d+(?:\.\d+)?)(?:px)?\s*$", re.IGNORECASE)
PACKAGE_DATE = re.compile(r"_(\d{8})/")


def family_for(path):
    for prefix, family in FAMILIES.items():
        if path.startswith(prefix + "_"):
            return family
    return None


def clean_name(value):
    return " ".join(value.replace("-", " ").replace("_", " ").split())


def title_for(path, kind):
    parts = path.split("/")
    stem = Path(parts[-1]).stem
    suffix = SIZE_SUFFIX.search(stem)
    variant = []
    if suffix:
        variant = [suffix.group(1)]
        if suffix.group(2):
            variant.append(suffix.group(2).title())
        stem = stem[:suffix.start()]

    prefix = {"service": "Arch_", "resource": "Res_", "category": "Arch-Category_"}.get(kind, "")
    if prefix and stem.startswith(prefix):
        stem = stem[len(prefix):]
    name = clean_name(stem)

    if kind == "service":
        category = clean_name(parts[1].removeprefix("Arch_"))
        title = f"Services / {category} / {name}"
    elif kind == "resource":
        category = clean_name(parts[1].removeprefix("Res_"))
        title = f"Resources / {category} / {name}"
    elif kind == "category":
        title = f"Categories / {name}"
    else:
        title = f"Architecture Groups / {name}"

    return f"{title} ({' '.join(variant)})" if variant else title


def dimension(value):
    match = NUMERIC_LENGTH.fullmatch(value or "")
    return float(match.group(1)) if match else None


def size_for(svg, path):
    root = ET.fromstring(svg)
    view_box = re.split(r"[\s,]+", root.attrib.get("viewBox", "").strip())
    vb_size = (float(view_box[2]), float(view_box[3])) if len(view_box) == 4 else (None, None)
    width = dimension(root.attrib.get("width")) or vb_size[0]
    height = dimension(root.attrib.get("height")) or vb_size[1]
    if width and height:
        return round(width, 3), round(height, 3)

    fallback = next((float(part) for part in reversed(path.split("/")[:-1]) if part.isdigit()), 48.0)
    return width or fallback, height or fallback


def version_for(paths):
    match = next((PACKAGE_DATE.search(info.filename) for info in paths if PACKAGE_DATE.search(info.filename)), None)
    if not match:
        return "undated"
    token = match.group(1)
    try:
        return datetime.strptime(token, "%m%d%Y").strftime("%Y-%m-%d")
    except ValueError:
        return token


def build(archive, output_dir):
    entries = defaultdict(list)
    with zipfile.ZipFile(archive) as source:
        paths = sorted(
            (info for info in source.infolist()
             if not info.is_dir() and not info.filename.startswith("__MACOSX/")
             and info.filename.lower().endswith(".svg")),
            key=lambda info: info.filename.casefold(),
        )
        version = version_for(paths)
        for info in paths:
            family = family_for(info.filename)
            if not family:
                continue
            filename, kind = family
            svg = source.read(info)
            width, height = size_for(svg, info.filename)
            entries[filename].append({
                # draw.io stores image data in semicolon-delimited styles, so omit the ;base64 marker.
                "data": "data:image/svg+xml," + base64.b64encode(svg).decode("ascii"),
                "w": width,
                "h": height,
                "aspect": "fixed",
                "title": title_for(info.filename, kind),
            })

    if not entries:
        raise ValueError("No SVG icons from the supported AWS icon folders were found")

    output_dir.mkdir(parents=True, exist_ok=True)
    current_dir = output_dir / "current"
    current_dir.mkdir(exist_ok=True)
    for filename, items in entries.items():
        root = ET.Element("mxlibrary")
        root.text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        output_path = output_dir / f"{filename}-{version}.xml"
        ET.ElementTree(root).write(
            output_path,
            encoding="utf-8",
            xml_declaration=True,
        )
        (current_dir / f"{filename}.xml").write_bytes(output_path.read_bytes())
        print(f"{filename}: {len(items)} SVG icons")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="AWS icon package ZIP")
    parser.add_argument("--output-dir", type=Path, default=Path("drawio_libs"))
    args = parser.parse_args()
    build(args.archive, args.output_dir)


if __name__ == "__main__":
    main()
