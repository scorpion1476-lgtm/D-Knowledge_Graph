"""Coordinator: parallel task planner with budgets, timeouts, and a policy gate."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..core.config import DKGConfig
from ..core.db import Database
from ..core.errors import BudgetExceededError, PolicyError
from ..core.policy import PolicyEngine, PolicyRequest
from .base import Agent, Task, TaskResult
from .budget import BudgetAccount
from .contradiction import ContradictionAgent
from .curation import CurationAgent
from .ingestion import IngestionAgent
from .ledger import AgentLedger
from .report import ReportAgent
from .research import ResearchAgent
from .security_review import SecurityReviewAgent
from .validation import ValidationAgent


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()


class Coordinator:
    def __init__(
        self,
        db: Database,
        *,
        cfg: DKGConfig,
        agents: list[Agent] | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.db = db
        self.cfg = cfg
        self.agents: list[Agent] = agents or [
            ResearchAgent(db),
            IngestionAgent(db),
            CurationAgent(db),
            ValidationAgent(db),
            ContradictionAgent(db),
            SecurityReviewAgent(db),
            ReportAgent(db),
        ]
        self.policy = policy or PolicyEngine(
            allow_outbound_network=cfg.network.allow_outbound
        )
        self.ledger = AgentLedger(db, cfg.ledger_path)

    def _select_agent(self, kind: str) -> Agent:
        for a in self.agents:
            if a.handles(kind):
                return a
        raise ValueError(f"no agent handles kind: {kind}")

    def submit(
        self,
        task: Task,
        *,
        parent_id: str | None = None,
        principal_permissions: frozenset[str] = frozenset({"read", "ingest", "curate", "export"}),
    ) -> Future[dict[str, Any]]:
        # Policy gate before spending any budget.
        action = _action_for_kind(task.kind)
        req = PolicyRequest(
            action=action,
            subject_kind="task",
            subject_id=task.kind,
            principal="user_local",
            principal_permissions=principal_permissions,
            external_effect=task.kind.startswith(("ingest.web", "ingest.rss", "backup.write")),
            network=task.kind.startswith(("ingest.web", "ingest.rss")),
        )
        decision = self.policy.evaluate(req)
        if decision.decision == "deny":
            raise PolicyError(f"denied: {decision.reason}")
        if decision.decision == "require_consent":
            raise PolicyError(f"consent required: {decision.reason}")

        agent = self._select_agent(task.kind)
        run_id = self.ledger.start(
            agent=agent.name,
            kind=task.kind,
            input_=task.input,
            parent_id=parent_id,
            budget_units=task.budget_units,
        )
        pool = ThreadPoolExecutor(max_workers=1)
        return pool.submit(self._execute, run_id, task, agent)

    def _execute(self, run_id: str, task: Task, agent: Agent) -> dict[str, Any]:
        budget = BudgetAccount(total=task.budget_units)
        started = time.monotonic()
        try:
            result: TaskResult = agent.run(task)
            budget.charge(int(result.used_units or 0))
        except BudgetExceededError as e:
            self.ledger.finish(
                run_id, status="error", output={}, error={"code": e.code, "message": str(e)}
            )
            return {"task_run_id": run_id, "ok": False, "error": str(e)}
        except Exception as e:
            self.ledger.finish(
                run_id, status="error", output={}, error={"code": "internal", "message": str(e)}
            )
            return {"task_run_id": run_id, "ok": False, "error": str(e)}
        elapsed = time.monotonic() - started
        if elapsed > task.timeout_seconds:
            self.ledger.finish(
                run_id, status="timeout", output={}, error={"code": "timeout"}
            )
            return {"task_run_id": run_id, "ok": False, "error": "timeout"}
        self.ledger.finish(
            run_id, status="ok" if result.ok else "error",
            output=result.output, error=result.error, used_units=budget.used,
        )
        return {"task_run_id": run_id, "ok": result.ok, "output": result.output, "error": result.error}

    def run_parallel(
        self,
        tasks: list[Task],
        *,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        max_workers = max(1, min(int(max_workers or self.cfg.orchestration.max_parallel_workers), 32))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fs = [pool.submit(self._one, t) for t in tasks]
            return [f.result() for f in fs]

    def _one(self, task: Task) -> dict[str, Any]:
        agent = self._select_agent(task.kind)
        run_id = self.ledger.start(
            agent=agent.name, kind=task.kind, input_=task.input,
            budget_units=task.budget_units,
        )
        return self._execute(run_id, task, agent)


def _action_for_kind(kind: str) -> str:
    if kind.startswith("ingest."):
        return "ingest.file"
    if kind.startswith("curate."):
        return "graph.mutate"
    if kind.startswith("export.") or kind.startswith("report."):
        return "export.dryrun"
    if kind.startswith("security."):
        return "audit.list"
    if kind.startswith("contradiction."):
        return "evidence.get"
    if kind.startswith("evidence."):
        return "evidence.get"
    if kind.startswith("research."):
        return "search.hybrid"
    return "status.get"


# --- convenience workflows -----------------------------------------


def run_workflow(db: Database, workflow: str, payload: dict, *, cfg: DKGConfig) -> dict:
    coord = Coordinator(db, cfg=cfg)
    if workflow == "research":
        tasks = [Task(kind="research.gather", input=payload)]
    elif workflow == "verify":
        claim_id = payload.get("claim_id")
        tasks = [Task(kind="evidence.validate", input={"claim_id": claim_id})]
    elif workflow == "contradiction":
        tasks = [Task(kind="contradiction.scan", input=payload)]
    elif workflow == "security-review":
        tasks = [Task(kind="security.scan", input=payload)]
    else:
        raise ValueError(f"unknown workflow: {workflow}")
    results = coord.run_parallel(tasks)
    return {"workflow": workflow, "results": results}
