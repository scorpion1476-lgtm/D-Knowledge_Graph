"""Embedding adapters: local models, plus an opt-in remote endpoint backend.

Three adapters implement :class:`EmbeddingAdapter`, and any of them can be chosen
by the caller through :func:`select_embedding_adapter`:

- :class:`Model2VecEmbeddingAdapter` is the real, local, permissive embedding
  model (model2vec, MIT, with permissive model weights). Inference is pure numpy
  on CPU and fully deterministic. The model is pre-staged and loaded
  local-files-only; it never downloads at runtime.
- :class:`HashingEmbeddingAdapter` maps tokens into a fixed-length vector via
  feature hashing. It is the zero-dependency fallback used when the real model
  is not installed or not pre-staged, so the core degrades gracefully.
- :class:`RemoteEndpointEmbeddingAdapter` speaks the ordinary embeddings HTTP
  endpoint shape over stdlib urllib. It is OFF by default, it refuses to run
  without an explicit egress opt-in and sends nothing when it refuses, and when
  it does run against a non-loopback endpoint it first prints a warning that
  names exactly which text would leave the machine. The warning is skipped only
  when the endpoint is on the loopback interface, where nothing leaves.

Air-gap default: with no configuration at all, selection never returns the
remote adapter and no adapter opens a socket. The remote backend requires three
deliberate acts before a single byte moves: naming a backend, naming an
endpoint, and opting in to egress.

Vectors are keyed by adapter name in the vector store, and the remote adapter's
name carries a digest of its endpoint and model, so no two backends and no two
endpoints can ever share a key.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_WORD = re.compile(r"[A-Za-z0-9]+")

# Environment configuration. Every one of these is unset by default.
BACKEND_ENV = "DKG_EMBEDDING_BACKEND"
EGRESS_ENV = "DKG_ALLOW_EGRESS"
REMOTE_ENDPOINT_ENV = "DKG_EMBEDDING_REMOTE_ENDPOINT"
REMOTE_MODEL_ENV = "DKG_EMBEDDING_REMOTE_MODEL"
REMOTE_DIMENSION_ENV = "DKG_EMBEDDING_REMOTE_DIMENSION"
REMOTE_TIMEOUT_ENV = "DKG_EMBEDDING_REMOTE_TIMEOUT"
REMOTE_API_KEY_ENV = "DKG_EMBEDDING_REMOTE_API_KEY"

BACKENDS = ("auto", "hashing", "model2vec", "remote")
DEFAULT_BACKEND = "auto"
DEFAULT_REMOTE_TIMEOUT = 30.0

# Repository root, used to prefer a pre-staged model directory during local and
# CI runs. When the package is installed elsewhere the staged path simply does
# not exist and the default model id (loaded offline from the local cache) is
# used instead.
_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_EMBEDDING_MODEL_ID = "minishlab/potion-base-8M"

# Process-wide cache of loaded models keyed by spec, so repeated selection in a
# single run does not reload the weights.
_MODEL_CACHE: dict[str, tuple[Any, int]] = {}


class EmbeddingAdapter(ABC):
    name: str
    dimension: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...


class HashingEmbeddingAdapter(EmbeddingAdapter):
    """Feature-hashing embedding. Offline, deterministic, no external calls."""

    name = "hashing"

    def __init__(self, dimension: int = 128) -> None:
        if dimension < 16 or dimension > 4096:
            raise ValueError("dimension must be between 16 and 4096")
        self.dimension = dimension

    def _index(self, token: str) -> int:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dimension
            for tok in _WORD.findall((t or "").lower()):
                vec[self._index(tok)] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    def available(self) -> tuple[bool, str]:
        return True, "built-in offline feature hashing"


def _default_embedding_spec() -> str:
    """Resolve the embedding model spec: env, then a staged dir, then the id."""
    env = os.environ.get("DKG_EMBEDDING_MODEL")
    if env:
        return env
    staged = _ROOT / "models" / "embeddings" / "potion-base-8M"
    if staged.exists():
        return str(staged)
    return DEFAULT_EMBEDDING_MODEL_ID


class Model2VecEmbeddingAdapter(EmbeddingAdapter):
    """Real local embedding via model2vec (MIT). Deterministic, CPU, offline.

    The model is pre-staged (see scripts/prestage_models.py) and loaded
    local-files-only. Loading enforces the Hugging Face offline flags so no
    network call is made at runtime; if the model is not present, ``available``
    reports false with an honest reason and callers fall back to keyword search.
    """

    name = "model2vec"

    def __init__(self, model: str | None = None) -> None:
        self._spec = model or _default_embedding_spec()
        self._model: Any | None = None
        self._dim: int | None = None
        self._error: str | None = None

    def _load(self) -> None:
        if self._model is not None or self._error is not None:
            return
        cached = _MODEL_CACHE.get(self._spec)
        if cached is not None:
            self._model, self._dim = cached
            return
        # Enforce air-gap: never reach out to a model hub at runtime.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from model2vec import StaticModel
        except Exception as e:  # import guarded; extra not installed
            self._error = f"model2vec not installed: {e!r} (install the 'embeddings' extra)"
            return
        try:
            self._model = StaticModel.from_pretrained(self._spec)
            probe = self._model.encode(["dimension probe"])
            self._dim = int(probe.shape[1])
            _MODEL_CACHE[self._spec] = (self._model, self._dim)
        except Exception as e:
            self._model = None
            self._error = (
                f"embedding model {self._spec!r} could not be loaded offline: {e!r}; "
                "pre-stage it with scripts/prestage_models.py or set DKG_EMBEDDING_MODEL"
            )

    @property
    def dimension(self) -> int:  # type: ignore[override]
        self._load()
        if self._dim is None:
            raise RuntimeError(self._error or "embedding model unavailable")
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        if self._model is None:
            raise RuntimeError(self._error or "embedding model unavailable")
        vectors = self._model.encode(list(texts))
        return [[float(x) for x in row] for row in vectors]

    def available(self) -> tuple[bool, str]:
        self._load()
        if self._model is not None:
            return True, f"model2vec {self._spec} (dim {self._dim})"
        return False, self._error or "model2vec unavailable"


class EgressNotPermittedError(RuntimeError):
    """Raised when a remote backend is asked to send text without an egress opt-in.

    The refusal happens before anything is serialised or sent, so nothing leaves
    the machine. It is an exception rather than a silent fallback: a caller that
    explicitly selected a remote backend must be told it did not run, not handed
    a different backend's vectors.
    """


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_loopback_endpoint(url: str) -> bool:
    """True when the endpoint's host is on this machine's loopback interface.

    A request to a loopback address never reaches a network, so it is the one
    case where the egress warning is skipped. Anything that cannot be proven to
    be loopback, including a name that is not resolved here, is treated as
    remote.
    """
    host = (urlsplit(url or "").hostname or "").strip()
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _urllib_transport(url: str, body: bytes, *, headers: dict[str, str], timeout: float) -> bytes:
    """POST ``body`` to ``url`` using the standard library only.

    No third-party HTTP client is introduced. The scheme is checked here as well
    as by the caller so this function cannot be used to read a local file.
    """
    import urllib.request

    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"remote embedding endpoint must be http or https, got {url!r}")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked above
        return bytes(response.read())


def _parse_embedding_response(raw: bytes, *, expected: int, dimension: int) -> list[list[float]]:
    """Decode an embeddings-endpoint response, failing loud on any surprise."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"remote embedding response was not JSON: {e!r}") from e
    rows: Any = None
    if isinstance(doc, dict):
        data = doc.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            rows = [d.get("embedding") for d in data]
        elif isinstance(doc.get("embeddings"), list):
            rows = doc["embeddings"]
    elif isinstance(doc, list):
        rows = doc
    if not isinstance(rows, list):
        raise RuntimeError(
            "remote embedding response shape not recognised; expected "
            "{'data': [{'embedding': [...]}]} or {'embeddings': [[...]]}"
        )
    if len(rows) != expected:
        raise RuntimeError(f"remote embedding response returned {len(rows)} vectors, expected {expected}")
    out: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != dimension:
            got = len(row) if isinstance(row, list) else type(row).__name__
            raise RuntimeError(f"remote embedding vector has dimension {got}, expected {dimension}")
        out.append([float(x) for x in row])
    return out


class RemoteEndpointEmbeddingAdapter(EmbeddingAdapter):
    """Embeddings from an HTTP endpoint, off by default and opt-in for egress.

    The request body is the ordinary embeddings-endpoint JSON shape,
    ``{"model": ..., "input": [...]}``, and both ``{"data": [{"embedding": ...}]}``
    and ``{"embeddings": [[...]]}`` responses are accepted, so a local inference
    server or a hosted one can be pointed at without new code.

    The dimension is declared rather than probed. Probing would mean sending text
    just to discover a number, which is exactly the surprise egress this backend
    exists to make impossible.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        model: str | None = None,
        dimension: int | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        allow_egress: bool | None = None,
        transport: Any | None = None,
    ) -> None:
        self.endpoint = (endpoint if endpoint is not None else os.environ.get(REMOTE_ENDPOINT_ENV, "")).strip()
        self.model = (model if model is not None else os.environ.get(REMOTE_MODEL_ENV, "")).strip()
        self._allow_egress = allow_egress
        self._api_key = api_key if api_key is not None else os.environ.get(REMOTE_API_KEY_ENV)
        self._transport = transport or _urllib_transport
        self._dim = self._resolve_dimension(dimension)
        self._timeout = self._resolve_timeout(timeout)
        # The adapter name is the vector-store key. Folding the endpoint and the
        # model into it means two remote endpoints, or one endpoint serving two
        # models, can never write into each other's vectors.
        digest = hashlib.sha256(f"{self.endpoint}|{self.model}".encode()).hexdigest()[:12]
        self.name = f"remote-{digest}" if self.endpoint else "remote-unconfigured"

    @staticmethod
    def _resolve_dimension(dimension: int | None) -> int | None:
        if dimension is not None:
            return int(dimension)
        raw = os.environ.get(REMOTE_DIMENSION_ENV, "").strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _resolve_timeout(timeout: float | None) -> float:
        if timeout is not None:
            return float(timeout)
        raw = os.environ.get(REMOTE_TIMEOUT_ENV, "").strip()
        if not raw:
            return DEFAULT_REMOTE_TIMEOUT
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_REMOTE_TIMEOUT
        return value if value > 0 else DEFAULT_REMOTE_TIMEOUT

    def egress_permitted(self) -> bool:
        """Whether egress has been opted in, by constructor flag or environment."""
        if self._allow_egress is not None:
            return bool(self._allow_egress)
        return _truthy(os.environ.get(EGRESS_ENV))

    @property
    def dimension(self) -> int:  # type: ignore[override]
        if self._dim is None:
            raise RuntimeError(
                "remote embedding backend has no declared dimension; set "
                f"{REMOTE_DIMENSION_ENV} or pass dimension=. It is never probed, "
                "because probing would send text off the machine."
            )
        return self._dim

    def available(self) -> tuple[bool, str]:
        if not self.endpoint:
            return False, f"remote embedding backend has no endpoint; set {REMOTE_ENDPOINT_ENV}"
        scheme = urlsplit(self.endpoint).scheme
        if scheme not in ("http", "https"):
            return False, f"remote embedding endpoint must be http or https, got {self.endpoint!r}"
        if self._dim is None:
            return False, f"remote embedding backend has no declared dimension; set {REMOTE_DIMENSION_ENV}"
        if not self.egress_permitted():
            return False, self._refusal_reason(texts=None)
        where = "loopback" if is_loopback_endpoint(self.endpoint) else "remote"
        return True, f"remote embedding endpoint {self.endpoint} ({where}, dim {self._dim}, egress opted in)"

    def _refusal_reason(self, *, texts: list[str] | None) -> str:
        count = "" if texts is None else f" {len(texts)} text(s) to"
        return (
            f"remote embedding backend refuses to run: sending{count} {self.endpoint} "
            f"is egress and egress has not been opted in. Set {EGRESS_ENV}=1 to opt in. "
            "Nothing was sent."
        )

    def _warn_egress(self, texts: list[str]) -> None:
        """Print, before sending, exactly which text would leave the machine."""
        stream = sys.stderr
        print(
            f"DKG EGRESS WARNING: the remote embedding backend is about to send "
            f"{len(texts)} text(s) to {self.endpoint}. This endpoint is not on the "
            f"loopback interface, so the following text will leave this machine:",
            file=stream,
        )
        for index, text in enumerate(texts, start=1):
            print(f"  [{index}] {text}", file=stream)
        if self.model:
            print(f"  model name sent with the request: {self.model}", file=stream)
        if self._api_key:
            print("  an Authorization header is attached to the request.", file=stream)
        print(
            f"DKG EGRESS WARNING: unset {EGRESS_ENV} to stop this backend sending anything.",
            file=stream,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        items = [str(t) for t in texts]
        if not self.endpoint:
            raise RuntimeError(f"remote embedding backend has no endpoint; set {REMOTE_ENDPOINT_ENV}")
        if urlsplit(self.endpoint).scheme not in ("http", "https"):
            raise RuntimeError(f"remote embedding endpoint must be http or https, got {self.endpoint!r}")
        dimension = self.dimension
        # The refusal is the first thing that happens after validation, before
        # any body is built and before any warning is printed, so a refused call
        # cannot leak the text through any path.
        if not self.egress_permitted():
            raise EgressNotPermittedError(self._refusal_reason(texts=items))
        if not items:
            return []
        if not is_loopback_endpoint(self.endpoint):
            self._warn_egress(items)
        payload: dict[str, Any] = {"input": items}
        if self.model:
            payload["model"] = self.model
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        raw = self._transport(self.endpoint, body, headers=headers, timeout=self._timeout)
        return _parse_embedding_response(raw, expected=len(items), dimension=dimension)


def select_embedding_adapter(
    backend: str | None = None, *, fallback_dimension: int = 256
) -> EmbeddingAdapter:
    """Return the embedding adapter the caller asked for.

    ``backend`` wins; otherwise the ``DKG_EMBEDDING_BACKEND`` environment
    variable is read; otherwise the default is ``auto``, which is the historic
    behaviour of preferring the real local model and falling back to hashing.
    ``remote`` is never reached unless it is named, so the air-gap default holds
    with no configuration at all.

    An explicitly named backend is returned even when it is unavailable, so the
    caller sees its honest reason instead of a silent substitution.
    """
    chosen = (backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
    if chosen in ("", "auto", "default"):
        real = Model2VecEmbeddingAdapter()
        if real.available()[0]:
            return real
        return HashingEmbeddingAdapter(dimension=fallback_dimension)
    if chosen == "hashing":
        return HashingEmbeddingAdapter(dimension=fallback_dimension)
    if chosen == "model2vec":
        return Model2VecEmbeddingAdapter()
    if chosen == "remote":
        return RemoteEndpointEmbeddingAdapter()
    raise ValueError(f"unknown embedding backend {chosen!r}; choose one of {', '.join(BACKENDS)}")


def default_embedding_adapter(*, fallback_dimension: int = 256) -> EmbeddingAdapter:
    """Return the configured embedding adapter, defaulting to the local one.

    This is the single selection point for the shared retrieval path. With no
    configuration it returns the real local model when the 'embeddings' extra is
    installed and the model is pre-staged, and the deterministic hashing fallback
    otherwise, so hybrid search degrades to keyword plus FTS with an honest
    reason recorded by the capability registry. Setting
    ``DKG_EMBEDDING_BACKEND`` selects a different backend.
    """
    return select_embedding_adapter(None, fallback_dimension=fallback_dimension)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector dimensions must match")
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)
