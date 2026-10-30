"""Read-only projection of the existing archive DB. No models or sidecars."""
import json
import math
import sqlite3
import sys
import collections
import datetime
import gzip
import io
import hashlib
import os
from contextlib import closing
from pathlib import Path


def snapshot(root, check_files=True):
    root = Path(root).resolve(strict=True)
    database = root / "_state" / "archive.sqlite3"
    if not database.resolve(strict=True).is_relative_to(root):
        raise ValueError("database_outside_library")
    rows = []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        connection.execute("PRAGMA query_only=ON")
        # Only archive metadata is projected. No source QQ messages, credentials,
        # model settings, image pixels or unrestricted SQL are returned.
        for sha, files_json, metadata_json in connection.execute("SELECT sha, files, metadata FROM images"):
            if len(rows) >= 50000:
                raise ValueError("library_snapshot_too_large")
            try:
                metadata = json.loads(metadata_json)
                files = []
                for relative in json.loads(files_json):
                    try:
                        relative = Path(relative)
                        if relative.is_absolute() or not relative.parts or relative.parts[0] not in ("SFW", "NSFW"):
                            continue
                        if ".." in relative.parts:
                            continue
                        if not check_files:
                            files.append(str(root / relative))
                            continue
                        file = (root / relative).resolve(strict=True)
                        if file.is_relative_to(root) and file.is_file() and 0 < file.stat().st_size <= 25 * 1024 * 1024:
                            files.append(str(file))
                    except (ValueError, TypeError, OSError):
                        continue
                if not files:
                    continue
                tags = {}
                groups = [("character", metadata.get("characters", {})), ("general", metadata.get("tags", {}))]
                groups += [(kind, values) for kind, values in metadata.get("additionalTags", {}).items() if kind in ("copyright", "meta")]
                for kind, values in groups:
                    for tag, score in values.items():
                        score = float(score)
                        if isinstance(tag, str) and math.isfinite(score) and 0 <= score <= 1:
                            tags[tag] = {"category": kind, "score": score}
                rows.append({"key": sha, "file": files[0], "tags": tags})
            except (ValueError, TypeError, KeyError, OSError, AttributeError):
                continue
    return rows


def compact_snapshot(root):
    rows = snapshot(root, check_files=False)
    vocabulary, ids = [], {}
    for row in rows:
        tags = []
        for tag, value in row["tags"].items():
            key = (tag, value["category"])
            if key not in ids:
                ids[key] = len(vocabulary)
                vocabulary.append([tag, value["category"]])
            tags.append(ids[key])
        row["tags"] = tags
    return {"rows": rows, "vocabulary": vocabulary}


def prepare_delivery(root, keys):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 40000000
    root = Path(root).resolve(strict=True)
    database = root / "_state" / "archive.sqlite3"
    if not database.resolve(strict=True).is_relative_to(root):
        raise ValueError("database_outside_library")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 9:
        raise ValueError("invalid_delivery_keys")
    cache = root / "_state" / "delivery-cache"
    cache.mkdir(exist_ok=True)
    if not cache.resolve(strict=True).is_relative_to(root) or cache.is_symlink():
        raise ValueError("invalid_delivery_cache")
    result = []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        for key in keys:
            if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("invalid_image_key")
            row = connection.execute("SELECT files FROM images WHERE sha=?", (key,)).fetchone()
            if not row:
                raise ValueError("image_not_in_database")
            original = None
            for relative in json.loads(row[0]):
                relative = Path(relative)
                if relative.is_absolute() or not relative.parts or relative.parts[0] not in ("SFW", "NSFW"):
                    continue
                file = (root / relative).resolve(strict=True)
                if file.is_relative_to(root) and file.is_file():
                    original = file
                    break
            if original is None:
                raise ValueError("original_image_missing")
            original_size = original.stat().st_size
            if not 0 < original_size <= 25 * 1024**2:
                raise ValueError("invalid_original_size")
            if original_size <= 2 * 1024**2:
                result.append({"key": key, "file": str(original), "bytes": original_size, "originalBytes": original_size, "prepared": False})
                continue
            target = cache / (key + "-2560-q88-v1.jpg")
            if not target.exists():
                data = original.read_bytes()
                if hashlib.sha256(data).hexdigest() != key:
                    raise ValueError("original_image_changed")
                with Image.open(io.BytesIO(data)) as image:
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError("animated_delivery_not_supported")
                    oriented = ImageOps.exif_transpose(image).convert("RGBA")
                    oriented.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
                    rgb = Image.new("RGB", oriented.size, "white")
                    rgb.paste(oriented, mask=oriented.getchannel("A"))
                    for side in (2560, 2048, 1600):
                        rgb.thumbnail((side, side), Image.Resampling.LANCZOS)
                        for quality in (88, 80, 72):
                            buffer = io.BytesIO()
                            rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
                            if buffer.tell() <= 2 * 1024**2:
                                break
                        if buffer.tell() <= 2 * 1024**2:
                            break
                    encoded = buffer.getvalue()
                    if len(encoded) > 2 * 1024**2:
                        raise ValueError("delivery_copy_too_large")
                temp = cache / (target.name + "." + str(os.getpid()) + ".tmp")
                with temp.open("xb") as stream:
                    stream.write(encoded)
                os.replace(temp, target)
            if target.is_symlink() or not target.resolve(strict=True).is_relative_to(cache.resolve()):
                raise ValueError("invalid_delivery_copy")
            result.append({"key": key, "file": str(target), "bytes": target.stat().st_size, "originalBytes": original_size, "prepared": True})
    # A bounded disposable cache, never remove archive originals or active copies.
    keep = {item["file"] for item in result}
    entries = sorted((file for file in cache.glob("*-2560-q88-v1.jpg") if not file.is_symlink()), key=lambda file: file.stat().st_mtime)
    total = sum(file.stat().st_size for file in entries)
    remaining = len(entries)
    for file in entries:
        if total <= 512 * 1024**2 and remaining <= 512:
            break
        if str(file) not in keep:
            size = file.stat().st_size
            file.unlink()
            total -= size
            remaining -= 1
    return result


def prepare_receipt_retry(root, item):
    """A one-time lossless copy of the exact image submitted to QQ."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 40000000
    root = Path(root).resolve(strict=True)
    key = item.get("key", "")
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid_image_key")
    database = (root / "_state" / "archive.sqlite3").resolve(strict=True)
    if not database.is_relative_to(root):
        raise ValueError("database_outside_library")
    cache = root / "_state" / "delivery-cache"
    cache.mkdir(exist_ok=True)
    if cache.is_symlink() or not cache.resolve(strict=True).is_relative_to(root):
        raise ValueError("invalid_delivery_cache")
    source = Path(item.get("file", "")).resolve(strict=True)
    allowed = {(cache / (key + "-2560-q88-v1.jpg")).resolve()}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        row = connection.execute("SELECT files FROM images WHERE sha=?", (key,)).fetchone()
        if not row:
            raise ValueError("image_not_in_database")
        for relative in json.loads(row[0]):
            relative = Path(relative)
            if not relative.is_absolute() and relative.parts and relative.parts[0] in ("SFW", "NSFW"):
                file = (root / relative).resolve()
                if file.is_relative_to(root):
                    allowed.add(file)
    if source not in allowed or not source.is_relative_to(root):
        raise ValueError("invalid_retry_source")
    if not 0 < source.stat().st_size <= 25 * 1024**2:
        raise ValueError("invalid_retry_source_size")
    data = source.read_bytes()
    source_sha = hashlib.sha256(data).hexdigest()
    if source.parent != cache.resolve() and source_sha != key:
        raise ValueError("original_image_changed")
    target = cache / (key + "-" + source_sha + "-tl25-minus10-v1.png")
    if not target.exists():
        with Image.open(io.BytesIO(data)) as original:
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError("animated_delivery_not_supported")
            image = ImageOps.exif_transpose(original).convert("RGBA")
            for y in range(min(5, image.height)):
                for x in range(min(5, image.width)):
                    r, g, b, a = image.getpixel((x, y))
                    image.putpixel((x, y), (max(0, r - 10), max(0, g - 10), max(0, b - 10), a))
            buffer = io.BytesIO()
            # PNG preserves every decoded pixel outside the 5x5 corner.
            image.save(buffer, format="PNG", optimize=True)
            encoded = buffer.getvalue()
        if len(encoded) > 25 * 1024**2:
            raise ValueError("receipt_retry_copy_too_large")
        temp = cache / (target.name + "." + str(os.getpid()) + ".tmp")
        try:
            with temp.open("xb") as stream:
                stream.write(encoded)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    if target.is_symlink() or not target.resolve(strict=True).is_relative_to(cache.resolve()):
        raise ValueError("invalid_receipt_retry_copy")
    # Keep these disposable variants bounded separately from normal copies.
    entries = sorted((p for p in cache.glob("*-tl25-minus10-v1.png") if not p.is_symlink()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in entries)
    remaining = len(entries)
    for file in entries:
        if total <= 128 * 1024**2 and remaining <= 64:
            break
        if file != target:
            try:
                size = file.stat().st_size
                file.unlink()
                total -= size
                remaining -= 1
            except FileNotFoundError:
                pass
    return {"key": key, "file": str(target), "bytes": target.stat().st_size,
            "sourceSha256": source_sha, "adjustment": "top-left-5x5-rgb-minus10"}


def overview(root, request):
    root = Path(root).resolve(strict=True)
    database = root / "_state" / "archive.sqlite3"
    if not database.resolve(strict=True).is_relative_to(root):
        raise ValueError("database_outside_library")
    counts = collections.Counter()
    tag_counts = {}
    total = valid = missing = unknown = size = 0
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        connection.execute("PRAGMA query_only=ON")
        for files_json, metadata_json in connection.execute("SELECT files, metadata FROM images"):
            total += 1
            try:
                meta = json.loads(metadata_json)
                rating = meta.get("category", "unknown")
                counts[rating] += 1
                unknown += not bool(meta.get("characters"))
                found = None
                for relative in json.loads(files_json):
                    try:
                        relative = Path(relative)
                        if relative.is_absolute() or not relative.parts or relative.parts[0] not in ("SFW", "NSFW"):
                            continue
                        file = (root / relative).resolve(strict=True)
                        if file.is_relative_to(root) and file.is_file():
                            found = file
                            break
                    except (ValueError, TypeError, OSError):
                        continue
                if found:
                    valid += 1
                    size += found.stat().st_size
                else:
                    missing += 1
                pools = {"character": meta.get("characters", {}), "general": meta.get("tags", {})}
                pools.update({kind: tags for kind, tags in meta.get("additionalTags", {}).items() if kind in ("copyright", "meta")})
                for kind, tags in pools.items():
                    for tag in tags:
                        entry = tag_counts.setdefault((kind, tag), {"tag": tag, "kind": kind, "count": 0, "sfw": 0, "nsfw": 0})
                        entry["count"] += 1
                        if rating in ("SFW", "NSFW"):
                            entry[rating.lower()] += 1
            except (ValueError, TypeError, AttributeError):
                continue
    here = Path(__file__).parent
    with gzip.open(here / "tag-zh.json.gz", "rt", encoding="utf-8") as file:
        translations = json.load(file)
    characters = json.loads((here / "characters.json").read_text(encoding="utf-8"))["characters"]
    overrides = {
        "1girl": "单个女孩", "1boy": "单个男孩", "solo": "单人", "looking_at_viewer": "看向观众",
        "closed_mouth": "闭口", "pantyhose": "连裤袜", "thighhighs": "过膝长袜",
        "black_pantyhose": "黑色连裤袜", "cosplay_photo": "角色扮演摄影", "copyright_logo": "版权标识",
        "aemeath_(wuthering_waves)": "爱弥斯（鸣潮）", "himesaki_rinami": "姬崎莉波",
        "rio_(blue_archive)": "调月莉音", "rio_(armed)_(blue_archive)": "调月莉音（战斗服）",
        "kei_(new_body)_(blue_archive)": "凯伊（新身体）",
    }
    rows = sorted(tag_counts.values(), key=lambda x: (-x["count"], x["tag"]))
    search_aliases = {
        "pantyhose": ["丝袜", "连裤袜"], "thighhighs": ["丝袜", "长筒袜", "过膝袜"], "stockings": ["丝袜", "长袜"],
        "black_pantyhose": ["黑丝", "丝袜"], "black_thighhighs": ["黑丝", "丝袜"],
        "white_pantyhose": ["白丝", "丝袜"], "white_thighhighs": ["白丝", "丝袜"],
        "white_hair": ["白毛"], "blonde_hair": ["金毛"],
    }
    for row in rows:
        name = characters.get(row["tag"], {}).get("name", "") if row["kind"] == "character" else ""
        if not any("\u3400" <= ch <= "\u9fff" for ch in name):
            name = translations.get(row["tag"], "")
        row["zh"] = overrides.get(row["tag"]) or name or "暂无中文译名（" + row["tag"] + "）"
    view = request.get("view", "summary")
    query = str(request.get("query", "")).strip().casefold()[:100]
    selected = [row for row in rows if view in ("summary", "search") or row["kind"] == view]
    if query:
        selected = [row for row in selected if any(query in str(value).casefold() for value in
                    [row["zh"], row["tag"], *search_aliases.get(row["tag"], []),
                     *characters.get(row["tag"], {}).get("aliases", [])])]
    page_count = max(1, math.ceil(len(selected) / 20))
    page = max(1, min(page_count, int(request.get("page", 1))))
    return {
        "asOf": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%m-%d %H:%M"),
        "total": total, "sfw": counts["SFW"], "nsfw": counts["NSFW"], "unknown": unknown,
        "valid": valid, "missing": missing, "mib": round(size / 1024**2, 1),
        "distinct": {kind: sum(row["kind"] == kind for row in rows) for kind in ("character", "general", "copyright", "meta")},
        "topCharacters": [row for row in rows if row["kind"] == "character"][:8],
        "topTags": [row for row in rows if row["kind"] == "general"][:12],
        "rows": selected if request.get("all") is True else selected[(page - 1) * 20:page * 20],
        "matches": len(selected), "page": page, "pages": page_count,
    }


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.read(65537).decode("utf-8"))
        mode = request.get("mode")
        if mode == "overview":
            result = {"ok": True, "report": overview(request["root"], request)}
        elif mode == "compact":
            result = {"ok": True, **compact_snapshot(request["root"])}
        elif mode == "prepare":
            result = {"ok": True, "rows": prepare_delivery(request["root"], request.get("keys"))}
        elif mode == "receipt_retry":
            result = {"ok": True, "rows": [prepare_receipt_retry(request["root"], request.get("item", {}))]}
        else:
            result = {"ok": True, "rows": snapshot(request["root"])}
    except Exception as error:
        result = {"ok": False, "error": str(error)[:300]}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
