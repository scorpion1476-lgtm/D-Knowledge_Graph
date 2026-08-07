"""Minimal language-server client over stdio.

Language servers are external processes invoked over the standard editor-tooling
protocol (JSON-RPC with Content-Length framing). They are never Python-linked:
each is spawned by a non-interactive subprocess with list arguments, no shell, a
bounded timeout, and a clean shutdown so no server process is left orphaned. The
servers are optional, pre-staged, and capability-detected; the code plane falls
back to structural analysis when none is available.

Only the small surface the code plane needs is implemented: initialize, open a
document, ask for a definition, and shut down. Server-to-client requests (for
example a configuration request) are answered with a null result so the server
proceeds. This client makes no network call.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_STAGED_BIN = _ROOT / "tools" / "lsp" / "node_modules" / ".bin"
_START_TIMEOUT = 30.0
_REQUEST_TIMEOUT = 20.0


def _node_dir() -> str | None:
    node = os.environ.get("DKG_NODE") or shutil.which("node")
    if node and Path(node).exists():
        return str(Path(node).resolve().parent)
    for cand in ("/usr/local/opt/node/bin", "/usr/local/bin", "/opt/homebrew/bin"):
        if (Path(cand) / "node").exists():
            return cand
    return None


def _resolve_binary(name: str, env_var: str) -> str | None:
    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override
    staged = _STAGED_BIN / name
    if staged.exists():
        return str(staged)
    return shutil.which(name)


def server_command(language: str) -> list[str] | None:
    """Return the server command for a language, or None when unavailable.

    Requires Node for the JavaScript-based servers; returns None when Node or the
    server binary is absent so the caller degrades to structural analysis.
    """
    if _node_dir() is None:
        return None
    if language == "python":
        b = _resolve_binary("pyright-langserver", "DKG_LSP_PYTHON")
        return [b, "--stdio"] if b else None
    if language in ("javascript", "typescript"):
        b = _resolve_binary("typescript-language-server", "DKG_LSP_JS")
        return [b, "--stdio"] if b else None
    return None


def server_init_options(language: str) -> dict:
    """Server-specific initialization options.

    The TypeScript server needs to be pointed at a TypeScript install; the staged
    one is used so the server works in any workspace.
    """
    if language in ("javascript", "typescript"):
        tsserver = _STAGED_BIN.parent / "typescript" / "lib" / "tsserver.js"
        if tsserver.exists():
            return {"tsserver": {"path": str(tsserver)}}
    return {}


def resolution_available(language: str) -> bool:
    return server_command(language) is not None


class LspClient:
    """A short-lived stdio client for one server process."""

    def __init__(
        self,
        command: list[str],
        root_uri: str,
        *,
        init_options: dict | None = None,
        request_timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        self._command = command
        self._root_uri = root_uri
        self._init_options = init_options or {}
        self._timeout = request_timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._responses: dict[int, dict] = {}
        self._cond = threading.Condition()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._alive = False

    # -- lifecycle ------------------------------------------------------

    def __enter__(self) -> LspClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def start(self) -> None:
        env = dict(os.environ)
        node_dir = _node_dir()
        if node_dir:
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        # A local server needs no network; do not pass proxy settings through.
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.pop(k, None)
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            bufsize=0,
        )
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self._request("initialize", {
                "processId": os.getpid(),
                "rootUri": self._root_uri,
                "capabilities": {"textDocument": {"definition": {"linkSupport": False}}},
                "initializationOptions": self._init_options,
            }, timeout=_START_TIMEOUT)
            self._notify("initialized", {})
        except Exception:
            # Never leave a half-started server orphaned.
            self._alive = False
            with contextlib.suppress(Exception):
                self._proc.kill()
                self._proc.wait(timeout=3.0)
            self._proc = None
            raise

    def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            self._request("shutdown", None, timeout=3.0)
        with contextlib.suppress(Exception):
            self._notify("exit", None)
        self._alive = False
        proc = self._proc
        try:
            proc.wait(timeout=3.0)
        except Exception:
            proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=3.0)
        for stream in (proc.stdin, proc.stdout):
            with contextlib.suppress(Exception):
                if stream:
                    stream.close()
        self._proc = None

    # -- protocol -------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        stdout = proc.stdout
        while self._alive:
            try:
                headers = b""
                while b"\r\n\r\n" not in headers:
                    ch = stdout.read(1)
                    if not ch:
                        return
                    headers += ch
                length = 0
                for line in headers.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1].strip())
                body = b""
                while len(body) < length:
                    chunk = stdout.read(length - len(body))
                    if not chunk:
                        return
                    body += chunk
                msg = json.loads(body)
            except Exception:
                return
            mid = msg.get("id")
            if mid is not None and ("result" in msg or "error" in msg):
                with self._cond:
                    self._responses[int(mid)] = msg
                    self._cond.notify_all()
            elif mid is not None and "method" in msg:
                # Server-to-client request: reply null so the server proceeds.
                self._reply(int(mid), None)
            # notifications (no id) are ignored (diagnostics, progress)

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("language server is not running")
        data = json.dumps(payload).encode("utf-8")
        header = b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
        with self._write_lock:
            proc.stdin.write(header + data)
            proc.stdin.flush()

    def _notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _reply(self, req_id: int, result: Any) -> None:
        with contextlib.suppress(Exception):
            self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _request(self, method: str, params: Any, *, timeout: float | None = None) -> Any:
        rid = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline_timeout = timeout if timeout is not None else self._timeout
        with self._cond:
            ok = self._cond.wait_for(lambda: rid in self._responses, timeout=deadline_timeout)
            if not ok:
                raise TimeoutError(f"language server request {method!r} timed out")
            msg = self._responses.pop(rid)
        if "error" in msg:
            raise RuntimeError(f"language server error on {method!r}: {msg['error']}")
        return msg.get("result")

    # -- operations -----------------------------------------------------

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        self._notify("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": text}
        })

    def definition(self, uri: str, line: int, character: int) -> list[dict]:
        """Return definition locations (possibly empty) for a position."""
        result = self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": int(line), "character": int(character)},
        })
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        return list(result)
