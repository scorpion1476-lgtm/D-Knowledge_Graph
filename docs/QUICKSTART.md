# Quick start (non-technical)

This is the shortest path from an empty folder to a working, offline
knowledge graph over your own notes.

## 1. Install once

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"
```

## 2. Initialise a home

```bash
./.venv/bin/dkg init
```

This creates `.dkg/` next to you. No files leave the machine.

## 3. Feed it some notes

Put a few markdown or plain-text files in a folder called `notes/` and run:

```bash
./.venv/bin/dkg ingest ./notes --recursive
```

## 4. Ask it questions

```bash
./.venv/bin/dkg search "topic you care about"
./.venv/bin/dkg graph  "a name you saw earlier" --depth 1
```

## 5. Prove where an answer came from

Pick a claim ID from the search output and run:

```bash
./.venv/bin/dkg evidence <claim-id>
```

## 6. Back it up

```bash
./.venv/bin/dkg backup --out backup.tar.gz
```

## What you did not do

- Sign up for any service.
- Send any note anywhere.
- Install a model.
- Trust any single command with more than the read permission it needs.
