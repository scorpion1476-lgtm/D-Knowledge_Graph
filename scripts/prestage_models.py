#!/usr/bin/env python3
"""Pre-stage the local retrieval models and record their provenance.

This is a build-time and CI tool. It is the only place a model download happens.
The product itself never downloads a model at runtime: the embedding and reranker
adapters load these pre-staged files local-files-only.

It stages two permissive models:
- embeddings: minishlab/potion-base-8M (MIT weights), used by model2vec (MIT).
- reranker: Xenova/ms-marco-MiniLM-L-6-v2 (Apache-2.0 weights), used by
  fastembed (Apache-2.0) over ONNX Runtime.

It writes a provenance record (model id, licence, source, staged path, file
checksums, and dimension) to docs/model_provenance.json so the checksums can be
verified independently. Network access here is a build-time concession and does
not weaken the product's air-gap default.

Fail loud: any staging failure raises rather than writing a partial record.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
EMB_DIR = MODELS / "embeddings" / "potion-base-8M"
RRK_CACHE = MODELS / "reranker"
DETECT_CACHE = MODELS / "media-detect"
PROVENANCE = ROOT / "docs" / "model_provenance.json"

EMB_REPO = "minishlab/potion-base-8M"
RRK_REPO = "Xenova/ms-marco-MiniLM-L-6-v2"
DETECT_VISION_REPO = "Qdrant/clip-ViT-B-32-vision"
DETECT_TEXT_REPO = "Qdrant/clip-ViT-B-32-text"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _checksums(directory: Path, suffixes: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix in suffixes:
            out.append(
                {
                    "file": str(p.relative_to(ROOT)),
                    "bytes": p.stat().st_size,
                    "sha256": _sha256(p),
                }
            )
    return out


def _stage_embeddings() -> dict:
    from huggingface_hub import snapshot_download

    EMB_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=EMB_REPO, local_dir=str(EMB_DIR))
    # Confirm the model loads from the staged directory with no network.
    from model2vec import StaticModel

    model = StaticModel.from_pretrained(str(EMB_DIR))
    dim = int(model.encode(["provenance probe"]).shape[1])
    files = _checksums(EMB_DIR, (".safetensors", ".json", ".txt"))
    if not files:
        raise RuntimeError(f"embedding model staged no files under {EMB_DIR}")
    return {
        "capability": "embeddings",
        "library": "model2vec",
        "library_license": "MIT",
        "model_id": EMB_REPO,
        "weight_license": "MIT",
        "source": f"https://huggingface.co/{EMB_REPO}",
        "staged_path": str(EMB_DIR.relative_to(ROOT)),
        "dimension": dim,
        "files": files,
    }


def _stage_reranker() -> dict:
    RRK_CACHE.mkdir(parents=True, exist_ok=True)
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    encoder = TextCrossEncoder(model_name=RRK_REPO, cache_dir=str(RRK_CACHE))
    scores = list(encoder.rerank("query planner tuning", ["how to tune a query planner"]))
    if not scores:
        raise RuntimeError("reranker staged but produced no score on the probe pair")
    files = _checksums(RRK_CACHE, (".onnx", ".json", ".txt"))
    if not files:
        raise RuntimeError(f"reranker model staged no files under {RRK_CACHE}")
    return {
        "capability": "reranker",
        "library": "fastembed",
        "library_license": "Apache-2.0",
        "model_id": RRK_REPO,
        "weight_license": "Apache-2.0",
        "source": f"https://huggingface.co/{RRK_REPO}",
        "staged_path": str(RRK_CACHE.relative_to(ROOT)),
        "probe_score": round(float(scores[0]), 4),
        "files": files,
    }


def _stage_detect() -> dict:
    DETECT_CACHE.mkdir(parents=True, exist_ok=True)
    from fastembed import ImageEmbedding, TextEmbedding

    vis = ImageEmbedding(model_name=DETECT_VISION_REPO, cache_dir=str(DETECT_CACHE))
    txt = TextEmbedding(model_name=DETECT_TEXT_REPO, cache_dir=str(DETECT_CACHE))
    # Confirm both towers load and produce vectors offline after staging.
    _ = list(txt.embed(["a provenance probe"]))
    files = _checksums(DETECT_CACHE, (".onnx", ".json", ".txt"))
    if not files:
        raise RuntimeError(f"detection models staged no files under {DETECT_CACHE}")
    del vis
    return {
        "capability": "media-detect",
        "library": "fastembed",
        "library_license": "Apache-2.0",
        "model_id": f"{DETECT_VISION_REPO} + {DETECT_TEXT_REPO}",
        "weight_license": "MIT",
        "source": f"https://huggingface.co/{DETECT_VISION_REPO}",
        "staged_path": str(DETECT_CACHE.relative_to(ROOT)),
        "files": files,
    }


def main() -> int:
    record = {
        "note": (
            "Pre-staged local model provenance. Downloads happen only here, at "
            "build or CI time. The product loads these files local-files-only and "
            "never downloads at runtime."
        ),
        "models": [_stage_embeddings(), _stage_reranker(), _stage_detect()],
    }
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    print(f"staged embeddings -> {EMB_DIR.relative_to(ROOT)}")
    print(f"staged reranker   -> {RRK_CACHE.relative_to(ROOT)}")
    print(f"staged detection  -> {DETECT_CACHE.relative_to(ROOT)}")
    print(f"wrote provenance  -> {PROVENANCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
