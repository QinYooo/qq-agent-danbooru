"""Conservative visual duplicate checks, independent of character and rating tags.

Exact byte duplicates belong to the caller's SHA256 index. This module catches
resizing/recompression and small visual changes without using a learned model.
It never reads, changes, or deletes files.
"""
from __future__ import annotations

import base64
import math
import re

import numpy as np
from PIL import Image

THUMB_SIZE = 32
_THUMB_BYTES = THUMB_SIZE * THUMB_SIZE * 3
_THUMB_BASE64_LENGTH = _THUMB_BYTES // 3 * 4
_HASH = re.compile(r"[0-9a-fA-F]{16}\Z")
_LUMA = np.asarray([.299, .587, .114], dtype=np.float64)
_N = np.arange(THUMB_SIZE, dtype=np.float64)
_K = np.arange(8, dtype=np.float64)[:, None]
_DCT = np.cos(np.pi / THUMB_SIZE * (_N + .5) * _K)
_DCT[0] *= 1 / np.sqrt(2)
_DCT *= np.sqrt(2 / THUMB_SIZE)


def _bits_hex(bits):
    return f"{sum(int(bit) << index for index, bit in enumerate(bits)):016x}"


def fingerprint(image):
    """Return JSON/SQLite-friendly hashes and a raw RGB comparison thumbnail."""
    if not isinstance(image, Image.Image) or min(image.size) < 1:
        raise ValueError("invalid_similarity_image")
    rgb = image.convert("RGB")
    thumb = np.asarray(rgb.resize((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS), dtype=np.uint8)
    gray = thumb.astype(np.float64) @ _LUMA
    coefficients = (_DCT @ gray @ _DCT.T).reshape(-1)
    # Exclude DC (overall luminance), and use 63 stable low-frequency bits.
    ac = coefficients[1:]
    phash = _bits_hex(ac > np.median(ac))
    small_gray = np.asarray(rgb.convert("L").resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    dhash = _bits_hex((small_gray[:, 1:] > small_gray[:, :-1]).reshape(-1))
    return {
        "phash": phash,
        "dhash": dhash,
        "thumbnail": base64.b64encode(thumb.tobytes()).decode("ascii"),
        "width": rgb.width,
        "height": rgb.height,
    }


def _distance_limit(value):
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 1 or numeric > 16:
        return None
    return int(numeric)


def _header(value):
    if not isinstance(value, dict):
        return None
    hashes = []
    for field in ("phash", "dhash"):
        encoded = value.get(field)
        if not isinstance(encoded, str) or not _HASH.fullmatch(encoded):
            return None
        hashes.append(int(encoded, 16))
    dimensions = []
    for field in ("width", "height"):
        size = value.get(field)
        if isinstance(size, bool) or not isinstance(size, (int, float)):
            return None
        if not math.isfinite(size) or size != int(size) or not 1 <= size <= 1000000:
            return None
        dimensions.append(int(size))
    return (*hashes, *dimensions)


def coarse_match(a, b, max_distance=4):
    """Cheap index candidate filter. Invalid data and distance 0 never match."""
    try:
        limit = _distance_limit(max_distance)
        left, right = _header(a), _header(b)
        if limit is None or left is None or right is None:
            return False
        aph, adh, aw, ah = left
        bph, bdh, bw, bh = right
        ar, br = aw / ah, bw / bh
        if abs(ar - br) / max(ar, br) > .03:
            return False
        if (aph ^ bph).bit_count() > limit:
            return False
        return (adh ^ bdh).bit_count() <= min(16, max(8, 2 * limit))
    except (TypeError, ValueError, OverflowError):
        return False


def _thumbnail(value):
    encoded = value.get("thumbnail")
    if not isinstance(encoded, str) or len(encoded) != _THUMB_BASE64_LENGTH:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeEncodeError):
        return None
    if len(raw) != _THUMB_BYTES:
        return None
    return np.frombuffer(raw, dtype=np.uint8).reshape((THUMB_SIZE, THUMB_SIZE, 3)).astype(np.float64) / 255


def _structured(gray):
    # Flat colors and smooth gradients produce many hash collisions. Let exact
    # SHA256 handle them rather than claiming confidence in a visual match.
    if float(gray.std()) < .025:
        return False
    lap = -4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    return float(np.mean(lap * lap)) >= .00015


def is_similar(a, b, max_distance=4):
    """Confirm a coarse candidate using color difference and image structure.

    Both mean RGB absolute error <= 0.06 and luminance correlation >= 0.985
    are required. The rule is deliberately conservative: crops, changed poses,
    and different artwork of the same character are not semantic duplicates.
    """
    try:
        if not coarse_match(a, b, max_distance):
            return False
        left, right = _thumbnail(a), _thumbnail(b)
        if left is None or right is None:
            return False
        if float(np.mean(np.abs(left - right))) > .06:
            return False
        lgray, rgray = left @ _LUMA, right @ _LUMA
        if not _structured(lgray) or not _structured(rgray):
            return False
        lv = (lgray - lgray.mean()).reshape(-1)
        rv = (rgray - rgray.mean()).reshape(-1)
        denominator = float(np.linalg.norm(lv) * np.linalg.norm(rv))
        if denominator <= 0 or not math.isfinite(denominator):
            return False
        correlation = float(np.dot(lv, rv) / denominator)
        return math.isfinite(correlation) and correlation >= .985
    except (TypeError, ValueError, OverflowError, KeyError):
        return False
