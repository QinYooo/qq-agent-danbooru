"""Local WD tagger worker. JSONL over stdio; never uploads an image."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from similarity import fingerprint, coarse_match, is_similar
from model_runtime import PixAITagger
from image_quality import ImageTypeClassifier, assess_scores

MODEL_ID = "SmilingWolf/wd-swinv2-tagger-v3"
MODEL_REVISION = "627aef95638667ddcaa3ac8ae625e88ea5b02f51"
MODEL_SHA256 = "e6774bff34d43bd49f75a47db4ef217dce701c9847b546523eb85ff6dbba1db1"
TAGS_SHA256 = "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"
DEFAULTS = {
    "minSide": 768, "minPixels": 786432, "characterThreshold": .85,
    "generalThreshold": .35, "filterThreshold": .6, "nsfwThreshold": .6,
    "ratingConfidence": .6, "maxBytes": 26214400, "maxPixels": 40000000,
    "maxAspect": 2.5, "nearDuplicateDistance": 4,
    "minEntropy": 3.0, "minSharpness": 12.0,
    "saveUnknownCharacters": True, "personThreshold": .7,
    "filterImageType": True, "minIllustrationScore": .65, "allow3d": False,
    "rejectScreenshots": True, "screenshotThreshold": .35, "explicitTagThreshold": .6,
}
FILTER_TAGS = ("meme", "text_focus", "comic", "speech_bubble", "sketch")
SCREENSHOT_TAGS = ("screenshot", "anime_screenshot", "game_screenshot", "thumbnail_collage")
# Exact tag names, not substring matching (e.g. 'ass' must never match 'glasses').
EXPLICIT_TAGS = (
    "pussy", "pussy_juice", "nipples", "areolae", "areola_slip", "anus", "penis",
    "breasts_out", "nude", "completely_nude", "sex", "vaginal", "anal", "fellatio",
    "cunnilingus", "masturbation", "handjob", "paizuri", "cum", "cum_in_pussy",
    "cum_on_body", "cum_in_mouth", "cum_overflow",
)
NSFW_POLICY = "explicit-tags-v1"
PERSON_TAGS = tuple([f"{n}{kind}" for kind in ("girl", "boy", "other") for n in (1,)]
                    + [f"{n}{kind}s" for kind in ("girl", "boy", "other") for n in (2, 3, 4, 5, "6+")]
                    + ["multiple_girls", "multiple_boys"])
RATINGS = ("general", "sensitive", "questionable", "explicit")
ALLOWED_HOSTS = ("qpic.cn", "qq.com", "gtimg.cn")
ALLOWED_EXACT_HOSTS = ("multimedia.nt.qq.com.cn",)
Image.MAX_IMAGE_PIXELS = 40000000
warnings.simplefilter("error", Image.DecompressionBombWarning)


class Rejected(Exception):
    pass


def settings(raw=None):
    result = dict(DEFAULTS)
    bounds = {
        "minSide": (64, 8192), "minPixels": (4096, 40000000),
        "characterThreshold": (.05, .999), "generalThreshold": (.05, .99),
        "filterThreshold": (.1, .99), "nsfwThreshold": (.01, .99),
        "ratingConfidence": (.25, .99), "maxBytes": (1024, 52428800),
        "maxPixels": (4096, 40000000), "maxAspect": (1, 10),
        "nearDuplicateDistance": (0, 10), "minEntropy": (0, 8),
        "minSharpness": (0, 500),
        "personThreshold": (.1, .99),
        "minIllustrationScore": (.4, .99), "screenshotThreshold": (.1, .99),
        "explicitTagThreshold": (.2, .99),
    }
    for key, (lo, hi) in bounds.items():
        try:
            value = float((raw or {}).get(key, result[key]))
            if math.isfinite(value):
                result[key] = min(hi, max(lo, value))
        except (TypeError, ValueError):
            pass
    result["saveUnknownCharacters"] = (raw or {}).get("saveUnknownCharacters", True) is True
    for key in ("filterImageType", "rejectScreenshots"):
        result[key] = (raw or {}).get(key, True) is True
    result["allow3d"] = (raw or {}).get("allow3d", False) is True
    return result


def validate_url(url):
    parts = urllib.parse.urlsplit(str(url))
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise Rejected("unsupported_image_url")
    if host not in ALLOWED_EXACT_HOSTS and not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS):
        raise Rejected("untrusted_image_host")
    if parts.port not in (None, 80, 443):
        raise Rejected("unsupported_image_port")
    return str(url)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bounded_read(stream, limit):
    data = bytearray()
    while True:
        chunk = stream.read(min(65536, limit + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > limit:
            raise Rejected("file_too_large")


def read_source(source, cfg):
    limit = int(cfg["maxBytes"])
    if not isinstance(source, dict):
        raise Rejected("missing_image_source")
    if source.get("url"):
        url = validate_url(source["url"])
        request = urllib.request.Request(url, headers={"User-Agent": "QQ-Agent-Danbooru/1.0"})
        opener = urllib.request.build_opener(SafeRedirect())
        try:
            with opener.open(request, timeout=25) as response:
                validate_url(response.geturl())
                length = response.headers.get("Content-Length", "0")
                if length.isdigit() and int(length) > limit:
                    raise Rejected("file_too_large")
                return bounded_read(response, limit)
        except Rejected:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            # URL signatures and credentials must not enter logs.
            code = getattr(exc, "code", None)
            raise RuntimeError("download_failed" + (f":HTTP_{code}" if code else ":network_error")) from None
    if source.get("path") and source.get("trusted") is True:
        text = str(source["path"])
        if text.startswith("file://"):
            parsed = urllib.parse.urlsplit(text)
            if parsed.netloc not in ("", "localhost"):
                raise Rejected("remote_file_path")
            text = urllib.request.url2pathname(parsed.path)
        # Only paths resolved by QQ get_image may be marked trusted by the bridge.
        path = Path(text)
        if not path.is_absolute() or text.startswith(("\\\\", "//")):
            raise Rejected("invalid_local_image_path")
        if path.is_symlink() or not path.is_file():
            raise Rejected("local_image_missing")
        if path.stat().st_size > limit:
            raise Rejected("file_too_large")
        with path.open("rb") as stream:
            return bounded_read(stream, limit)
    if source.get("base64"):
        encoded = str(source["base64"])
        if encoded.startswith("base64://"):
            encoded = encoded[9:]
        if encoded.startswith("data:image/") and ";base64," in encoded:
            encoded = encoded.split(";base64,", 1)[1]
        if len(encoded) > (limit + 2) // 3 * 4 + 4:
            raise Rejected("file_too_large")
        try:
            result = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise Rejected("invalid_image_base64") from None
        if len(result) > limit:
            raise Rejected("file_too_large")
        return result
    raise Rejected("missing_image_source")


def decode_image(data, cfg):
    try:
        original = Image.open(io.BytesIO(data))
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "GIF": ".gif"}.get(original.format)
        if not ext:
            raise Rejected("unsupported_image_format")
        width, height = original.size
        if width * height > cfg["maxPixels"]:
            raise Rejected("too_many_pixels")
        if getattr(original, "n_frames", 1) > 1:
            raise Rejected("animated_image")
        if min(width, height) < cfg["minSide"] or width * height < cfg["minPixels"]:
            raise Rejected("low_resolution")
        if max(width, height) / min(width, height) > cfg["maxAspect"]:
            raise Rejected("extreme_aspect_ratio")
        original.load()
        oriented = ImageOps.exif_transpose(original).convert("RGBA")
        rgb = Image.new("RGB", oriented.size, "white")
        rgb.paste(oriented, mask=oriented.getchannel("A"))
        return rgb, ext
    except Rejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Rejected("invalid_or_unsafe_image") from None


def image_metrics(image):
    gray = ImageOps.grayscale(image)
    gray.thumbnail((512, 512), Image.Resampling.LANCZOS)
    arr = np.asarray(gray, dtype=np.float32)
    hist = np.asarray(gray.histogram(), dtype=np.float64)
    prob = hist[hist > 0] / hist.sum()
    entropy = float(-(prob * np.log2(prob)).sum())
    lap = -4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2] + arr[1:-1, 2:]
    sharpness = float(lap.var()) if lap.size else 0.0
    # dHash catches small resizes/recompressions; never compare across character sets.
    tiny = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (tiny[:, 1:] > tiny[:, :-1]).flatten()
    dhash = sum(int(bit) << index for index, bit in enumerate(bits))
    return {"entropy": round(entropy, 4), "sharpness": round(sharpness, 4), "dhash": f"{dhash:016x}"}


def preprocess(image, size):
    side = max(image.size)
    square = Image.new("RGB", (side, side), "white")
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    square = square.resize((size, size), Image.Resampling.BICUBIC)
    return np.asarray(square, dtype=np.float32)[:, :, ::-1][None, ...].copy()


class Tagger:
    model_id = MODEL_ID
    revision = MODEL_REVISION
    model_sha = MODEL_SHA256
    provider = "CPUExecutionProvider"
    def __init__(self, model_dir):
        import onnxruntime as ort
        model_dir = Path(model_dir)
        model_path = model_dir / "model.onnx"
        with model_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != MODEL_SHA256:
            raise RuntimeError("model_checksum_mismatch")
        with (model_dir / "selected_tags.csv").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != TAGS_SHA256:
                raise RuntimeError("tag_file_checksum_mismatch")
        with (model_dir / "selected_tags.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.tags = list(csv.DictReader(stream))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 2) // 2))
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0]
        self.size = int(self.input.shape[1])
        if self.session.get_outputs()[0].shape[-1] != len(self.tags):
            raise RuntimeError("model_tag_count_mismatch")
        names = {tag["name"] for tag in self.tags}
        if not set(FILTER_TAGS + RATINGS).issubset(names):
            raise RuntimeError("model_tag_schema_mismatch")

    def predict(self, image):
        values = self.session.run(None, {self.input.name: preprocess(image, self.size)})[0][0]
        output = {"general": {}, "characters": {}, "rating": {}}
        for tag, value in zip(self.tags, values):
            key = {"0": "general", "4": "characters", "9": "rating"}.get(tag["category"])
            if key:
                output[key][tag["name"]] = float(value)
        return output


def person_evidence(prediction, cfg):
    return {name: round(value, 5) for name in PERSON_TAGS
            if math.isfinite(value := prediction["general"].get(name, 0)) and value >= cfg["personThreshold"]}


def screenshot_evidence(prediction, cfg):
    values = {**prediction.get("general", {}), **prediction.get("meta", {})}
    return {name: round(values[name], 5) for name in SCREENSHOT_TAGS if values.get(name, 0) >= cfg["screenshotThreshold"]}


def explicit_evidence(prediction, cfg):
    return {name: round(prediction["general"][name], 5) for name in EXPLICIT_TAGS if prediction["general"].get(name, 0) >= cfg["explicitTagThreshold"]}


def nsfw_category(prediction, cfg):
    rating = prediction["rating"]
    if any(name not in rating or not math.isfinite(rating[name]) or not 0 <= rating[name] <= 1 for name in RATINGS):
        raise Rejected("invalid_rating_output")
    # The user's strict collection policy requires explicit content evidence.
    # Body shape, clothing, and the broad 'sensitive' rating are not sufficient.
    if explicit_evidence(prediction, cfg):
        return "NSFW"
    if max(rating.values()) < cfg["ratingConfidence"]:
        raise Rejected("uncertain_rating")
    if max(rating["questionable"], rating["explicit"]) >= cfg["nsfwThreshold"]:
        # Do not quietly move conflicting adult predictions into the SFW collection.
        raise Rejected("uncertain_nsfw_evidence")
    return "SFW"


def classify(prediction, cfg):
    if cfg["rejectScreenshots"] and (signals := screenshot_evidence(prediction, cfg)):
        raise Rejected("screenshot_tag:" + max(signals, key=signals.get))
    for tag in FILTER_TAGS:
        if prediction["general"].get(tag, 0) >= cfg["filterThreshold"]:
            raise Rejected("filtered_tag:" + tag)
    characters = sorted((name, value) for name, value in prediction["characters"].items() if value >= cfg["characterThreshold"])
    if not characters:
        if not cfg["saveUnknownCharacters"]:
            raise Rejected("unknown_or_uncertain_character")
        if not person_evidence(prediction, cfg) or prediction["general"].get("no_humans", 0) >= cfg["filterThreshold"]:
            raise Rejected("no_reliable_person")
    return nsfw_category(prediction, cfg), characters


def folder_name(tag):
    # Preserve Danbooru spelling unless Windows forbids it. Hash makes collisions impossible in practice.
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", tag).rstrip(" .")
    if clean.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        clean = "_" + clean
    if not clean or len(clean) > 90 or clean != tag:
        clean = (clean[:80] or "character") + "_" + hashlib.sha256(tag.encode()).hexdigest()[:8]
    return clean


def write_atomic(path, data):
    temp = path.with_name(path.name + ".partial")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class Archive:
    def __init__(self, root, tagger, image_type=None):
        self.root = Path(root).resolve()
        self.tagger = tagger
        self.image_type = image_type
        self.model_meta = {
            "model": getattr(tagger, "model_id", MODEL_ID),
            "modelRevision": getattr(tagger, "revision", MODEL_REVISION),
            "modelSha256": getattr(tagger, "model_sha", MODEL_SHA256),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("SFW", "NSFW", "_state", "_logs"):
            (self.root / name).mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "_state" / "archive.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS images (sha TEXT PRIMARY KEY, dhash TEXT, signature TEXT, width INT, height INT, files TEXT, metadata TEXT, created REAL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS image_signature ON images(signature)")
        self.db.execute("CREATE TABLE IF NOT EXISTS fingerprints (sha TEXT PRIMARY KEY, phash TEXT, dhash TEXT, width INT, height INT, thumbnail TEXT, files TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS library_files (path TEXT PRIMARY KEY, size INT, mtime_ns INT, sha TEXT)")
        self.stats = {"saved": 0, "rejected": 0, "duplicate": 0, "error": 0}
        self.last_scan = 0
        self.sync_library()

    def close(self):
        self.db.close()

    def _forget_path(self, rel, sha):
        row = self.db.execute("SELECT files FROM fingerprints WHERE sha=?", (sha,)).fetchone()
        if row:
            files = [p for p in json.loads(row[0]) if p != rel]
            if files:
                self.db.execute("UPDATE fingerprints SET files=? WHERE sha=?", (json.dumps(files), sha))
            else:
                self.db.execute("DELETE FROM fingerprints WHERE sha=?", (sha,))
        self.db.execute("DELETE FROM library_files WHERE path=?", (rel,))

    def index_image(self, sha, fp, files):
        old = self.db.execute("SELECT files FROM fingerprints WHERE sha=?", (sha,)).fetchone()
        merged = sorted(set(files) | set(json.loads(old[0]) if old else []))
        self.db.execute("INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?,?,?)", (sha, fp["phash"], fp["dhash"], fp["width"], fp["height"], fp["thumbnail"], json.dumps(merged)))
        for rel in files:
            stat = (self.root / rel).stat()
            self.db.execute("INSERT OR REPLACE INTO library_files VALUES (?,?,?,?)", (rel, stat.st_size, stat.st_mtime_ns, sha))

    def sync_library(self):
        # Include existing and manually added archive images, without modifying those files.
        # Metadata avoids rehashing unchanged files; no text or model reclassification is needed.
        known = {row[0]: row[1:] for row in self.db.execute("SELECT path,size,mtime_ns,sha FROM library_files")}
        present = set()
        cfg = settings({"minSide": 64, "minPixels": 4096, "maxAspect": 10})
        for group in ("SFW", "NSFW"):
            for directory, subdirs, names in os.walk(self.root / group, followlinks=False):
                subdirs[:] = [name for name in subdirs if not (Path(directory) / name).is_symlink()
                              and (Path(directory) / name).resolve().is_relative_to(self.root)]
                for name in names:
                    path = Path(directory) / name
                    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
                        continue
                    if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                        continue
                    rel = str(path.relative_to(self.root))
                    present.add(rel)
                    try:
                        stat = path.stat()
                        previous = known.get(rel)
                        if previous and previous[:2] == (stat.st_size, stat.st_mtime_ns):
                            continue
                        if previous:
                            self._forget_path(rel, previous[2])
                        if stat.st_size > cfg["maxBytes"]:
                            continue
                        data = path.read_bytes()
                        image, _ = decode_image(data, cfg)
                        self.index_image(hashlib.sha256(data).hexdigest(), fingerprint(image), [rel])
                    except (OSError, Rejected, ValueError):
                        continue
        for rel, (_, _, sha) in known.items():
            if rel not in present:
                self._forget_path(rel, sha)
        self.db.commit()
        self.last_scan = time.monotonic()

    def near_duplicate(self, fp, distance):
        if distance <= 0:
            return None
        for sha, phash, dhash, width, height in self.db.execute("SELECT sha,phash,dhash,width,height FROM fingerprints"):
            candidate = {"phash": phash, "dhash": dhash, "width": width, "height": height}
            if not coarse_match(fp, candidate, distance):
                continue
            row = self.db.execute("SELECT thumbnail,files FROM fingerprints WHERE sha=?", (sha,)).fetchone()
            candidate["thumbnail"] = row[0]
            files = json.loads(row[1])
            valid_files = self.valid_index_files(sha, files)
            if valid_files and is_similar(fp, candidate, distance):
                return {"sha256": sha, "file": valid_files[0]}
        return None

    def valid_index_files(self, sha, files):
        valid = []
        for rel in files:
            try:
                path = self.root / rel
                if not path.resolve().is_relative_to(self.root) or not path.is_file():
                    continue
                row = self.db.execute("SELECT size,mtime_ns,sha FROM library_files WHERE path=?", (rel,)).fetchone()
                stat = path.stat()
                if row == (stat.st_size, stat.st_mtime_ns, sha):
                    valid.append(rel)
            except OSError:
                continue
        return valid

    def record(self, result, meta=None):
        result = dict(result)
        status = result.get("status", "error")
        if status not in self.stats:
            status = result["status"] = "error"
        self.stats[status] += 1
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **result, "meta": clean_meta(meta)}
        # One file per day, then 10 MiB shards. No image or download URL is logged.
        stem = self.root / "_logs" / time.strftime("%Y-%m-%d")
        logfile = stem.with_suffix(".jsonl")
        if logfile.exists() and logfile.stat().st_size > 10 * 1024 * 1024:
            logfile = self.root / "_logs" / (stem.name + "-" + str(time.time_ns()) + ".jsonl")
            os.replace(stem.with_suffix(".jsonl"), logfile)
            logfile = stem.with_suffix(".jsonl")
        with logfile.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        result["stats"] = dict(self.stats)
        write_atomic(self.root / "_state" / "status.json", json.dumps({"updatedAt": entry["time"], **self.model_meta, "stats": self.stats, "last": result}, ensure_ascii=False, indent=2).encode("utf-8"))
        return result

    def process(self, request):
        started = time.monotonic()
        result = {"id": request.get("id"), "status": "error"}
        cfg = settings(request.get("settings"))
        meta = clean_meta(request.get("meta"))
        try:
            if request.get("command") == "record":
                result.update(status=request.get("status", "rejected"), reason=str(request.get("reason", "unspecified"))[:200])
                return self.record(result, meta)
            data = read_source(request.get("source"), cfg)
            if time.monotonic() - self.last_scan > 30:
                self.sync_library()
            sha = hashlib.sha256(data).hexdigest()
            result["sha256"] = sha
            row = self.db.execute("SELECT files FROM fingerprints WHERE sha=?", (sha,)).fetchone()
            if row and self.valid_index_files(sha, json.loads(row[0])):
                result.update(status="duplicate", reason="exact_duplicate")
                return self.record(result, meta)
            image, ext = decode_image(data, cfg)
            result["dimensions"] = [image.width, image.height]
            metrics = image_metrics(image)
            result["quality"] = metrics
            if metrics["entropy"] < cfg["minEntropy"] and metrics["sharpness"] < cfg["minSharpness"]:
                raise Rejected("flat_or_strongly_blurred_image")
            fp = fingerprint(image)
            duplicate = self.near_duplicate(fp, cfg["nearDuplicateDistance"])
            if duplicate:
                result.update(status="duplicate", reason="near_duplicate", duplicateOf=duplicate["sha256"], duplicateFile=duplicate["file"])
                return self.record(result, meta)
            if cfg["filterImageType"] and self.image_type is not None:
                assessment = assess_scores(self.image_type.predict(image), cfg["minIllustrationScore"], cfg["allow3d"])
                result["typeAssessment"] = assessment
                if not assessment["accepted"]:
                    raise Rejected(assessment["reason"])
            prediction = self.tagger.predict(image)
            result["screenshotEvidence"] = screenshot_evidence(prediction, cfg)
            result["explicitEvidence"] = explicit_evidence(prediction, cfg)
            result["rating"] = {k: round(v, 5) for k, v in prediction["rating"].items()}
            group, characters = classify(prediction, cfg)
            result["characters"] = [tag for tag, _ in characters]
            result["characterStatus"] = "identified" if characters else "unidentified"
            result["personEvidence"] = person_evidence(prediction, cfg)
            signature = group + ":" + "|".join(result["characters"])
            # Leave reserve for the database and logs; never fill the drive with images.
            if shutil.disk_usage(self.root).free < max(512 * 1024 * 1024, len(data) * len(characters) * 2):
                raise RuntimeError("insufficient_disk_space")
            tags = [name for name, _ in characters] + [name for name, value in sorted(prediction["general"].items(), key=lambda item: -item[1]) if value >= cfg["generalThreshold"]]
            metadata = {
                "schemaVersion": 2, "sha256": sha, **self.model_meta, "trainingTags": tags, "nsfwPolicy": NSFW_POLICY,
                "category": group, "rating": result["rating"], "characters": dict(characters),
                "typeAssessment": result.get("typeAssessment"), "screenshotEvidence": result["screenshotEvidence"], "explicitEvidence": result["explicitEvidence"],
                "characterStatus": result["characterStatus"], "personEvidence": result["personEvidence"],
                "tags": {tag: round(prediction["general"][tag], 5) for tag in tags if tag in prediction["general"]},
                "additionalTags": {category: {name: round(value, 5) for name, value in prediction.get(category, {}).items() if value >= cfg["generalThreshold"]} for category in ("meta", "copyright")},
                "width": image.width, "height": image.height, "quality": metrics,
                "settings": cfg, "source": meta, "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            files, created = [], []
            try:
                directories = [folder_name(max(characters, key=lambda item: item[1])[0])] if characters else ["_unknown_character"]
                for dirname in directories:
                    directory = self.root / group / dirname
                    directory.mkdir(parents=True, exist_ok=True)
                    image_path = directory / (sha + ext)
                    if image_path.exists():
                        with image_path.open("rb") as existing:
                            if hashlib.file_digest(existing, "sha256").hexdigest() != sha:
                                # Preserve a manually replaced/edited library file rather than
                                # pairing its pixels with this new image's labels and fingerprint.
                                image_path = directory / (sha + "-restored-" + str(time.time_ns()) + ext)
                    if not image_path.exists():
                        if files:
                            try:
                                os.link(self.root / files[0], image_path)
                            except OSError:
                                write_atomic(image_path, data)
                        else:
                            write_atomic(image_path, data)
                        created.append(image_path)
                    files.append(str(image_path.relative_to(self.root)))
                self.db.execute("INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?,?)", (sha, metrics["dhash"], signature, image.width, image.height, json.dumps(files), json.dumps(metadata), time.time()))
                self.index_image(sha, fp, files)
                self.db.commit()
            except Exception:
                self.db.rollback()
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            result.update(status="saved", reason="accepted_training_candidate" if characters else "accepted_unknown_character_candidate", category=group, files=files)
        except Rejected as exc:
            result.update(status="rejected", reason=str(exc))
        except Exception as exc:
            # Known short error codes are useful; arbitrary exception text could contain signed URLs.
            message = str(exc)
            result.update(status="error", reason=message if re.fullmatch(r"[a-z_]+(?::[A-Za-z0-9_]+)?", message) else type(exc).__name__)
        result["elapsedMs"] = round((time.monotonic() - started) * 1000)
        return self.record(result, meta)


def clean_meta(meta):
    if not isinstance(meta, dict):
        return {}
    return {key: str(meta[key])[:80] for key in ("groupId", "messageId", "senderId", "forwardMessageId", "forwardDepth") if key in meta}


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--image-type-model-dir", default="models/anime-image-type")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    archive = None
    try:
        tagger = Tagger(args.model_dir) if (Path(args.model_dir) / "model.onnx").is_file() else PixAITagger(args.model_dir)
        image_type = ImageTypeClassifier(args.image_type_model_dir)
        archive = Archive(args.output_root, tagger, image_type)
        emit({"event": "ready", "model": tagger.model_id, "revision": tagger.revision, "provider": tagger.provider, "inputSize": tagger.size})
        while True:
            line = sys.stdin.buffer.readline(75 * 1024 * 1024)
            if not line:
                break
            if not line.endswith(b"\n"):
                raise RuntimeError("oversized_json_request")
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request_must_be_object")
                if request.get("command") == "shutdown":
                    break
                emit(archive.process(request))
            except (json.JSONDecodeError, ValueError):
                emit({"status": "error", "reason": "invalid_json_request"})
    except Exception as exc:
        emit({"event": "fatal", "reason": type(exc).__name__, "message": str(exc)[:300] if "http" not in str(exc).lower() else "startup_failed"})
        return 1
    finally:
        if archive:
            archive.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
