"""Pinned official PixAI adapter, entirely local after installation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

PIXAI_ID = "pixai-labs/pixai-tagger-v1.0"
PIXAI_REVISION = "9fe10addf9326e292da8a85a98ea74cd91b41771"
PIXAI_FILES = {
    "model.safetensors": "f29e475205cbcbc25b52a075840c7809d6215be15f45375113ba09c49bf90292",
    "tagger_pipeline.py": "3431279fdfff234f62664999fc1fe6bb20f1e8756f7435d0e165ebd4456f7fcd",
    "config.json": "f8a19b38661c37dc5fd519f2137be70f6f897972021b4ebc17b7d35a0e27b9dd",
    "preprocessor_config.json": "20451575e627950993de7b00641d5b263eceb3e7436e4ad518fea2411b60e49d",
}
RATING_NAMES = {"rating:g": "general", "rating:s": "sensitive", "rating:q": "questionable", "rating:e": "explicit"}


def grouped_scores(tags, categories, scores):
    if len(tags) != len(scores) or sum(count for _, count in categories) != len(tags):
        raise RuntimeError("model_tag_count_mismatch")
    output = {"general": {}, "characters": {}, "rating": {}, "meta": {}, "copyright": {}}
    offset = 0
    for category, count in categories:
        target = "characters" if category == "character" else category
        if target in output:
            for name, score in zip(tags[offset:offset + count], scores[offset:offset + count]):
                if target == "rating":
                    name = RATING_NAMES.get(name)
                    if name is None:
                        raise RuntimeError("model_rating_schema_mismatch")
                output[target][name] = float(score)
        offset += count
    if set(output["rating"]) != set(RATING_NAMES.values()):
        raise RuntimeError("model_rating_schema_mismatch")
    return output


class PixAITagger:
    model_id = PIXAI_ID
    revision = PIXAI_REVISION
    model_sha = PIXAI_FILES["model.safetensors"]
    size = 1008

    def __init__(self, model_dir):
        model_dir = Path(model_dir).resolve()
        for name, expected in PIXAI_FILES.items():
            with (model_dir / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise RuntimeError("pixai_checksum_mismatch:" + name.replace(".", "_"))
        # Import only the locally reviewed and checksum-verified official architecture.
        # Never fetch or execute repository code via trust_remote_code.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import torch
        self.torch = torch
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError("pixai_requires_cuda")
        self.dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        self.provider = "CUDA: " + torch.cuda.get_device_name(0)
        spec = importlib.util.spec_from_file_location("pinned_pixai_architecture", model_dir / "tagger_pipeline.py")
        official = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = official
        spec.loader.exec_module(official)
        self.model = official.ViTDetCls.from_pretrained(str(model_dir), local_files_only=True, use_safetensors=True, dtype=self.dtype).to("cuda").eval()
        self.processor = official.RescalePadProcessor(size=self.size)
        self.tags = self.model.config.tags
        self.categories = self.model.config.tags_split
        if len(self.tags) != 30877 or dict(self.categories).get("character") != 8308:
            raise RuntimeError("model_tag_schema_mismatch")
        # Validate names and offsets even before the first real image is received.
        grouped_scores(self.tags, self.categories, [0.] * len(self.tags))

    def predict(self, image):
        torch = self.torch
        try:
            with torch.inference_mode():
                tensor = self.processor.preprocess(image, return_tensors="pt")["pixel_values"].to(device="cuda", dtype=self.dtype)
                scores = self.model(tensor).float().sigmoid()[0].cpu().numpy()
            return grouped_scores(self.tags, self.categories, scores)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            raise RuntimeError("gpu_out_of_memory") from None
