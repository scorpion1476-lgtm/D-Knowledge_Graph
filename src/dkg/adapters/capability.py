"""Capability registry.

A capability is a named optional feature. Adapters register themselves as
providers of one or more capabilities and report their availability. The
registry exposes ``describe()`` for the CLI ``doctor`` and MCP ``capabilities``
tool, and ``require(name)`` for callers that need to hard-fail when a
capability is missing.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.errors import AdapterUnavailableError


@dataclass
class Capability:
    name: str
    description: str
    check: Callable[[], tuple[bool, str]]
    kind: str = "adapter"  # 'adapter' | 'runtime' | 'format' | 'protocol'


@dataclass
class CapabilityRegistry:
    capabilities: dict[str, Capability] = field(default_factory=dict)

    def register(self, cap: Capability) -> None:
        self.capabilities[cap.name] = cap

    def describe(self) -> list[dict]:
        out = []
        for cap in sorted(self.capabilities.values(), key=lambda c: c.name):
            available, reason = _safe_check(cap)
            out.append(
                {
                    "name": cap.name,
                    "description": cap.description,
                    "kind": cap.kind,
                    "available": available,
                    "reason": reason,
                }
            )
        return out

    def available(self, name: str) -> bool:
        cap = self.capabilities.get(name)
        if cap is None:
            return False
        return _safe_check(cap)[0]

    def require(self, name: str) -> None:
        cap = self.capabilities.get(name)
        if cap is None:
            raise AdapterUnavailableError(f"capability {name!r} is not registered")
        ok, reason = _safe_check(cap)
        if not ok:
            raise AdapterUnavailableError(f"capability {name!r} is unavailable: {reason}")


def _safe_check(cap: Capability) -> tuple[bool, str]:
    try:
        return cap.check()
    except Exception as e:  # capability checks must never crash the CLI
        return False, f"check raised: {e!r}"


def _module_available(module: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module)
        return True, "installed"
    except ImportError as e:
        return False, f"missing dependency: {e.name or module}"


def _llm_available() -> tuple[bool, str]:
    # Wire the bundled deterministic adapter so the LLM interface is a live
    # runtime dependency, not just a description string.
    from .llm import DeterministicLLMAdapter

    return DeterministicLLMAdapter().available()


def _media_image_check() -> tuple[bool, str]:
    from ..media.capability import have_pillow

    if have_pillow():
        return True, "Pillow available"
    return False, "install the 'media-image' extra (Pillow)"


def _media_ocr_check() -> tuple[bool, str]:
    from ..media.capability import tesseract_path

    p = tesseract_path()
    return (True, f"tesseract at {p}") if p else (False, "tesseract binary not installed")


def _media_video_check() -> tuple[bool, str]:
    from ..media.capability import ffprobe_path

    p = ffprobe_path()
    return (True, f"ffprobe at {p}") if p else (False, "ffprobe binary not installed")


def _media_asr_check() -> tuple[bool, str]:
    from ..media.asr import available

    return available()


def _code_resolve_check(language: str) -> tuple[bool, str]:
    from ..code.lsp import server_command

    cmd = server_command(language)
    if cmd is None:
        return False, f"no {language} language server staged (needs Node and the server)"
    return True, f"{language} language server: {cmd[0]}"


def _code_dataflow_check() -> tuple[bool, str]:
    from ..code.capability import tree_sitter_available

    if tree_sitter_available():
        return True, "built-in intra-procedural dataflow (needs the 'code' extra)"
    return False, "install the 'code' extra (tree-sitter)"


def _media_keyframe_check() -> tuple[bool, str]:
    from ..media.capability import ffmpeg_path, keyframe_ocr_ready

    if not ffmpeg_path():
        return False, "ffmpeg binary not installed"
    if keyframe_ocr_ready():
        return True, "ffmpeg present; tesseract present for on-screen OCR"
    return True, "ffmpeg present; tesseract absent so on-screen OCR is disabled"


def _media_detect_check() -> tuple[bool, str]:
    from ..media.detect import ImageDetector

    return ImageDetector().available()


def _code_parse_check() -> tuple[bool, str]:
    from ..code.capability import available_languages, tree_sitter_available

    if not tree_sitter_available():
        return False, "install the 'code' extra (tree-sitter and grammars)"
    langs = available_languages()
    if not langs:
        return False, "tree-sitter present but no grammar installed"
    return True, f"languages: {', '.join(langs)}"


def _embedding_default_check() -> tuple[bool, str]:
    from .embedding import default_embedding_adapter

    ad = default_embedding_adapter()
    ok, why = ad.available()
    return ok, f"active adapter: {ad.name} ({why})"


def _embedding_model_check() -> tuple[bool, str]:
    from .embedding import Model2VecEmbeddingAdapter

    return Model2VecEmbeddingAdapter().available()


def _reranker_check() -> tuple[bool, str]:
    from .reranker import CrossEncoderReranker

    return CrossEncoderReranker().available()


def _ariadne_check() -> tuple[bool, str]:
    try:
        import dkg.ariadne  # noqa: F401
    except Exception as e:
        return False, f"ariadne refinement detector not installed: {e!r}"
    return True, "ariadne refinement detector available"


def default_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()

    reg.register(
        Capability(
            name="ingest.html",
            description="HTML ingestion via beautifulsoup4 + lxml (optional extra 'html')",
            check=lambda: _module_available("bs4"),
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="ingest.pdf",
            description="PDF ingestion via pypdf (optional extra 'pdf')",
            check=lambda: _module_available("pypdf"),
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="ingest.rss",
            description="RSS/Atom ingestion: built-in stdlib parser; feedparser optional",
            check=lambda: (True, "built-in stdlib parser available; feedparser optional"),
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="ingest.docx",
            description="DOCX ingestion via stdlib zipfile and ElementTree",
            check=lambda: (True, "built-in stdlib DOCX text extractor"),
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="net.http",
            description="Outbound HTTP fetch via urllib (stdlib) or httpx (optional)",
            check=lambda: (True, "urllib bundled; httpx optional"),
            kind="runtime",
        )
    )
    reg.register(
        Capability(
            name="adapter.llm",
            description="LLM adapter: built-in DeterministicLLMAdapter is the default",
            check=_llm_available,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="adapter.embedding",
            description="Embedding adapter: real model2vec when available, else the hashing fallback",
            check=_embedding_default_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="adapter.embedding.model2vec",
            description="Real local embeddings via model2vec (optional 'embeddings' extra, pre-staged model)",
            check=_embedding_model_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="adapter.reranker",
            description="Local cross-encoder reranker via fastembed (optional 'reranker' extra, pre-staged model)",
            check=_reranker_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="graph.community",
            description="Community detection (Mnemosyne, built-in, modularity optimization)",
            check=lambda: (True, "built-in Mnemosyne detector"),
            kind="runtime",
        )
    )
    reg.register(
        Capability(
            name="graph.community.ariadne",
            description="Ariadne refinement detector (runs by default alongside the Mnemosyne base pass)",
            check=_ariadne_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="adapter.browser",
            description="Read-only browser research adapter (stdlib urllib)",
            check=lambda: (True, "built-in urllib-based read-only browser"),
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="adapter.identity",
            description="Local identity adapter mapping subjects to principals",
            check=lambda: (True, "built-in LocalIdentityAdapter"),
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="mcp.stdio",
            description="Local stdio MCP server (always available)",
            check=lambda: (True, "built-in"),
            kind="protocol",
        )
    )
    reg.register(
        Capability(
            name="mcp.http",
            description="Self-hosted Streamable HTTP MCP server (loopback default)",
            check=lambda: (True, "built-in; disabled by default in configuration"),
            kind="protocol",
        )
    )
    reg.register(
        Capability(
            name="media.image",
            description="Image ingestion: decode and EXIF via Pillow (optional extra 'media-image')",
            check=_media_image_check,
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="media.ocr",
            description="Image OCR via the external tesseract binary (Apache-2.0)",
            check=_media_ocr_check,
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="media.video",
            description="Video container and stream metadata via external ffprobe (carve-out)",
            check=_media_video_check,
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="media.asr",
            description="Audio transcription via whisper.cpp or faster-whisper (pre-staged model)",
            check=_media_asr_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="code.parse",
            description="Source-code parsing via Tree-sitter (optional 'code' extra)",
            check=_code_parse_check,
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="media.keyframes",
            description="Video keyframe and scene detection, and on-screen keyframe OCR (external ffmpeg and tesseract)",
            check=_media_keyframe_check,
            kind="format",
        )
    )
    reg.register(
        Capability(
            name="media.detect",
            description="Image object and content detection via zero-shot CLIP (optional 'media-detect' extra, pre-staged models)",
            check=_media_detect_check,
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="code.flow",
            description="Structural execution-flow tracing over the code graph (built-in, over-approximate)",
            check=lambda: (True, "built-in forward call-flow traversal"),
            kind="runtime",
        )
    )
    reg.register(
        Capability(
            name="code.resolve.python",
            description="Type-aware Python resolution via an external language server over stdio (pre-staged)",
            check=lambda: _code_resolve_check("python"),
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="code.resolve.javascript",
            description="Type-aware JavaScript resolution via an external language server over stdio (pre-staged)",
            check=lambda: _code_resolve_check("javascript"),
            kind="adapter",
        )
    )
    reg.register(
        Capability(
            name="code.dataflow",
            description="Intra-procedural def-use dataflow and optional taint (built-in, needs the 'code' extra)",
            check=_code_dataflow_check,
            kind="runtime",
        )
    )
    return reg
