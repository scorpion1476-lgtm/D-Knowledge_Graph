"""Minimal, JSON-RPC-2.0 MCP-like transports.

We ship a stdio server and a bind-loopback HTTP server. They speak a small
JSON-RPC surface that mirrors a subset of the Model Context Protocol so that
compatible clients can call read-only tools without D-Knowledge_Graph having a
mandatory dependency on any external MCP framework.
"""
