"""Sticky pull-request comment publication: one thread, updated in place.

A review that opens a new comment on every push buries the pull request. This
module keeps exactly one comment per marker: before every write it LOOKS THE
MARKER UP, updates the comment it finds, and creates one only when there is
none. The lookup is not an optimisation that can be skipped on a fast path; it
is the only thing standing between the design and a duplicated thread, so it is
unconditional and it happens first.

**The transport is injected.** Every request goes through a callable the caller
supplies, so the whole publication path is driven in tests by a fake that
records requests and simulates the comment store. No test in this project makes
a network call, and none needs to. The real transport over ``urllib`` from the
standard library is built explicitly by ``urllib_transport`` and by nothing
else; importing this module opens no socket and adds no dependency.

**Egress is opt-in and CI-only.** The product default is air-gapped. Posting a
comment is an explicit, deliberate outbound call that a caller has to ask for
by constructing a transport; nothing here reaches the network as a side effect
of local analysis.

**The artifact is validated before it is posted.** In the fork-safe two-stage
design the body arrives as an artifact produced by an unprivileged run over
code the pull request controls. The privileged stage that posts it therefore
treats it as untrusted input: it must be UTF-8, within GitHub's size limit,
carry exactly one marker, carry that marker on its first line, and contain no
raw HTML beyond the marker itself. A body that fails is rejected loudly rather
than posted with a warning.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..core.errors import ValidationError
from .pr_comment import MARKER_NAMESPACE, marker_for

DEFAULT_API_BASE = "https://api.github.com"

#: The only host a token is sent to unless the operator names another.
_PUBLIC_API_HOST = "api.github.com"

#: Operator opt-in for a self-hosted forge, as a space or comma separated list.
_API_HOST_ENV = "DKG_PR_API_HOSTS"

# GitHub rejects an issue comment over 65536 characters. The cap is a little
# under that so a body that passes here cannot be refused at the far end.
MAX_COMMENT_BYTES = 65000

# Bound on the marker lookup: one hundred comments a page, ten pages. A pull
# request with more than a thousand comments is pathological, and an unbounded
# scan on a hostile thread is a denial of service against our own run.
COMMENTS_PER_PAGE = 100
MAX_LOOKUP_PAGES = 10

API_ACCEPT = "application/vnd.github+json"
API_VERSION_HEADER = "2022-11-28"
USER_AGENT = "D-Knowledge_Graph-pr-review"

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$")
_MARKER_LINE_RE = re.compile(rf"^<!-- {re.escape(MARKER_NAMESPACE)}:[A-Za-z0-9._-]{{1,64}} -->$")


@dataclass(frozen=True)
class Request:
    """One outbound HTTP request, as the transport receives it."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None

    def redacted(self) -> dict:
        """A loggable form with the credential removed.

        Anything that prints a request must go through this. A token in a CI log
        is a token in a public place.
        """
        safe = {k: ("<redacted>" if k.lower() == "authorization" else v) for k, v in self.headers.items()}
        return {"method": self.method, "url": self.url, "headers": safe, "body_bytes": len(self.body or b"")}


@dataclass(frozen=True)
class Response:
    """One inbound HTTP response."""

    status: int
    body: str

    def json(self) -> object:
        try:
            return json.loads(self.body or "null")
        except ValueError as e:
            raise ValidationError(f"the API returned a body that is not JSON: {e}") from e


Transport = Callable[[Request], Response]


# -- validation --------------------------------------------------------------


def validate_comment_body(
    body: str, *, marker: str, max_bytes: int = MAX_COMMENT_BYTES
) -> dict:
    """Check a rendered comment before it is posted anywhere.

    Returns a verdict rather than raising, so the caller can report every reason
    at once instead of the first one. Reasons are sorted, so the verdict is
    deterministic and can be compared between runs.
    """
    reasons: list[str] = []
    text = body if isinstance(body, str) else ""
    if not isinstance(body, str):
        reasons.append("the body is not text")
    encoded = text.encode("utf-8", errors="replace")

    if not text.strip():
        reasons.append("the body is empty")
    if len(encoded) > max_bytes:
        reasons.append(f"the body is {len(encoded)} bytes, over the {max_bytes} byte limit")

    count = text.count(marker)
    if count == 0:
        reasons.append("the body carries no marker, so it cannot be a sticky review comment")
    elif count > 1:
        reasons.append(f"the body carries the marker {count} times; exactly one is required")

    first_line = text.split("\n", 1)[0].strip()
    if first_line != marker:
        reasons.append("the marker is not the first line of the body")
    if not _MARKER_LINE_RE.match(first_line):
        reasons.append("the first line is not a well-formed marker")

    # The renderer escapes every angle bracket in every value it lays out, and
    # it writes none of its own, so the ONLY `<` and `>` a legitimate body can
    # contain are the two in the marker. Checking that invariant is stronger
    # than a blocklist of tag names: a blocklist fails by omission, whereas an
    # angle bracket outside the marker proves the body did not come from the
    # renderer, whatever the tag happens to be called.
    without_marker = text.replace(marker, "", 1)
    if "<" in without_marker or ">" in without_marker:
        reasons.append(
            "the body contains a raw angle bracket outside the marker, so it did "
            "not come from the renderer, whose escape emits none"
        )

    # One HTML comment, the marker. Anything else means something reached the
    # body without going through the renderer's escape.
    if text.count("<!--") != 1 or text.count("-->") != 1:
        reasons.append("the body contains an HTML comment other than the marker")

    return {
        "valid": not reasons,
        "reasons": sorted(set(reasons)),
        "bytes": len(encoded),
        "marker": marker,
        "marker_count": count,
        "why": (
            "the body is treated as untrusted input. In the fork-safe two-stage "
            "design it was rendered by an unprivileged run over code the pull "
            "request controls, and it is posted by a privileged one, so it is "
            "checked rather than trusted."
        ),
    }


# -- request construction ----------------------------------------------------


def _check_repo(repo: str) -> str:
    value = str(repo or "").strip()
    if not _REPO_RE.match(value):
        raise ValidationError(f"repository must be OWNER/NAME, got {value!r}")
    return value


def _check_pr(pr_number: object) -> int:
    try:
        number = int(str(pr_number).strip())
    except (TypeError, ValueError) as e:
        raise ValidationError(f"pull-request number must be an integer, got {pr_number!r}") from e
    if number <= 0:
        raise ValidationError(f"pull-request number must be positive, got {number}")
    return number


def _check_api_base(api_base: str) -> str:
    """Validate the endpoint a bearer token would be sent to.

    The scheme alone is not enough. This value arrives from a command-line flag
    and from an action input, so a workflow that derives it from event data
    could otherwise be talked into sending the token anywhere: an adversarial
    review demonstrated that ``https://evil.example``, the userinfo form
    ``https://api.github.com@evil.example`` (whose host is evil.example, and
    which survives eyeballing), and the cloud metadata address
    ``https://169.254.169.254`` were all accepted. The host is therefore pinned
    to an allowlist: the public API, or a self-hosted host the operator names
    explicitly in DKG_PR_API_HOSTS.
    """
    value = str(api_base or "").strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise ValidationError(
            f"the API base must be an https URL with no query or fragment, got {api_base!r}"
        )
    # urlsplit puts any userinfo in netloc but not in hostname, so comparing
    # hostname is what defeats the "@" form; comparing netloc would not.
    if parts.username is not None or parts.password is not None:
        raise ValidationError(
            f"the API base must not carry credentials in the URL, got {api_base!r}"
        )
    host = (parts.hostname or "").lower()
    if host not in _permitted_api_hosts():
        raise ValidationError(
            f"refusing to send a token to {host!r}: it is not the public API host and is not "
            f"named in {_API_HOST_ENV}. Set that variable to a self-hosted host you trust."
        )
    return value


def _permitted_api_hosts() -> frozenset[str]:
    """The public API host, plus any host the operator has explicitly named."""
    import os

    extra = {
        h.strip().lower()
        for h in (os.environ.get(_API_HOST_ENV) or "").replace(",", " ").split()
        if h.strip()
    }
    return frozenset({_PUBLIC_API_HOST, *extra})


def _headers(token: str) -> dict[str, str]:
    if not str(token or "").strip():
        raise ValidationError("a token is required to write a pull-request comment")
    return {
        "Accept": API_ACCEPT,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": API_VERSION_HEADER,
    }


def _expect(response: Response, *, allowed: tuple[int, ...], what: str) -> object:
    if response.status not in allowed:
        raise ValidationError(f"{what} failed with HTTP {response.status}")
    return response.json()


# -- the marker lookup -------------------------------------------------------


def find_marked_comment(
    transport: Transport,
    *,
    repo: str,
    pr_number: int,
    marker: str,
    token: str,
    api_base: str = DEFAULT_API_BASE,
    max_pages: int = MAX_LOOKUP_PAGES,
) -> dict | None:
    """The existing comment carrying this marker, or None.

    Pages are read in ascending order and the FIRST match wins, which is the
    oldest marked comment. If a previous failure ever left two, the same one is
    updated on every subsequent run rather than the choice drifting.
    """
    repo = _check_repo(repo)
    pr_number = _check_pr(pr_number)
    api_base = _check_api_base(api_base)
    headers = _headers(token)
    pages = max(1, min(int(max_pages), 100))

    for page in range(1, pages + 1):
        url = (
            f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
            f"?per_page={COMMENTS_PER_PAGE}&page={page}"
        )
        response = transport(Request("GET", url, headers))
        payload = _expect(response, allowed=(200,), what="the comment lookup")
        if not isinstance(payload, list):
            raise ValidationError("the comment lookup returned something other than a list")
        for comment in payload:
            if not isinstance(comment, dict):
                continue
            if marker in str(comment.get("body") or ""):
                return {"id": comment.get("id"), "body": comment.get("body")}
        if len(payload) < COMMENTS_PER_PAGE:
            return None  # the last page; no need to ask for another
    return None


# -- publication -------------------------------------------------------------


def publish_sticky_comment(
    *,
    transport: Transport,
    repo: str,
    pr_number: int,
    body: str,
    token: str,
    marker_key: str | None = None,
    marker: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    dry_run: bool = False,
    max_bytes: int = MAX_COMMENT_BYTES,
    max_pages: int = MAX_LOOKUP_PAGES,
) -> dict:
    """Validate a body and put it in the one comment this marker owns.

    The order is fixed and not conditional: validate, look the marker up, then
    write. A caller cannot reach the write without the lookup having run, which
    is what makes duplication impossible rather than unlikely.

    ``dry_run`` performs the validation and the lookup and stops before the
    write, so a run can report what it would do without doing it.
    """
    resolved_marker = marker if marker is not None else marker_for(marker_key or "")
    validation = validate_comment_body(body, marker=resolved_marker, max_bytes=max_bytes)
    if not validation["valid"]:
        return {
            "action": "rejected",
            "posted": False,
            "comment_id": None,
            "marker": resolved_marker,
            "validation": validation,
            "requests": 0,
            "why": (
                "the rendered report failed validation, so nothing was posted. A "
                "body that cannot be shown to have come from the renderer is not "
                "published with a warning; it is refused."
            ),
        }

    repo = _check_repo(repo)
    pr_number = _check_pr(pr_number)
    api_base = _check_api_base(api_base)
    headers = _headers(token)

    existing = find_marked_comment(
        transport,
        repo=repo,
        pr_number=pr_number,
        marker=resolved_marker,
        token=token,
        api_base=api_base,
        max_pages=max_pages,
    )
    payload = json.dumps({"body": body}, ensure_ascii=False).encode("utf-8")

    if dry_run:
        return {
            "action": "would-update" if existing else "would-create",
            "posted": False,
            "comment_id": (existing or {}).get("id"),
            "marker": resolved_marker,
            "validation": validation,
            "requests": 0,
            "why": "dry run: the marker was looked up and no write was issued",
        }

    if existing is not None and existing.get("id") is not None:
        url = f"{api_base}/repos/{repo}/issues/comments/{int(existing['id'])}"
        response = transport(Request("PATCH", url, headers, payload))
        result = _expect(response, allowed=(200,), what="the comment update")
        action = "updated"
    else:
        url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
        response = transport(Request("POST", url, headers, payload))
        result = _expect(response, allowed=(201, 200), what="the comment creation")
        action = "created"

    comment_id = result.get("id") if isinstance(result, dict) else None
    return {
        "action": action,
        "posted": True,
        "comment_id": comment_id,
        "marker": resolved_marker,
        "validation": validation,
        "requests": 1,
        "why": (
            "the marker was looked up before this write, so the same comment is "
            "reused on every push and the thread is never duplicated"
        ),
    }


# -- the real transport (constructed explicitly, never by import) ------------


def urllib_transport(*, timeout: float = 15.0, max_response_bytes: int = 8 * 1024 * 1024) -> Transport:
    """A transport over the standard library, for the CI publication step only.

    Built by an explicit call, never on import, so nothing in the product
    reaches the network as a side effect. No third-party HTTP dependency is
    added: ``urllib`` ships with Python.
    """
    import urllib.error
    import urllib.request

    class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
        """Never follow a redirect while carrying a bearer token.

        The standard library's redirect handler strips only ``content-length``
        and ``content-type`` when it rebuilds the request; ``Authorization``
        survives, and the new location may be a different host or plain http.
        Validating the API base once therefore constrains the first hop and not
        where the token actually lands, which an adversarial review
        demonstrated. There is no legitimate redirect on this API path, so the
        safe behaviour is to refuse rather than to re-validate and continue.
        """

        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
            raise ValidationError(
                f"refusing to follow a {code} redirect to {newurl!r} while holding a token; "
                "the request was not retried and no credential was sent onward"
            )

    # Built per call rather than installed globally, so nothing else in the
    # process has its URL handling changed as a side effect.
    opener = urllib.request.build_opener(_RefuseRedirects)

    def send(request: Request) -> Response:
        _check_api_base(request.url.split("/repos/", 1)[0])
        req = urllib.request.Request(  # noqa: S310 - scheme and host are checked above
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            with opener.open(req, timeout=timeout) as handle:  # noqa: S310
                return Response(handle.status, handle.read(max_response_bytes).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(max_response_bytes).decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            raise ValidationError(f"the pull-request API could not be reached: {e.reason}") from e

    return send
