"""Security helpers: SSRF guard, redaction, XML/prompt/decompression checks.

These are used by the ingestion pipeline, the MCP surface, and any adapter
that reaches beyond the local filesystem.
"""
