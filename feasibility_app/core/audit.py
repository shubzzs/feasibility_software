"""Append-only audit trail per run.

Records: timestamp, input snapshot hash, formula id/version, intermediate
values, retrieved chunks (ids + pdf hash), LLM prompts/responses, verification
outcomes. A run must be reproducible from this log.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, run_id: str, out_dir: str | Path = "data/processed/audit"):
        self.run_id = run_id
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.out_dir / f"{run_id}.jsonl"
        self._lock = threading.Lock()

    def log(self, event: str, **payload: Any) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **payload,
        }
        line = json.dumps(rec, default=str, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    # convenience wrappers -------------------------------------------------- #
    def inputs_snapshot(self, ctx_dict: dict[str, Any]) -> str:
        blob = json.dumps(ctx_dict, sort_keys=True, default=str)
        digest = hashlib.sha256(blob.encode()).hexdigest()
        self.log("inputs_snapshot", sha256=digest, context=ctx_dict)
        return digest

    def stage_result(self, stage: str, status: str, values: list[dict[str, Any]],
                     flags: list[dict[str, Any]]) -> None:
        self.log("stage_result", stage=stage, status=status,
                 n_values=len(values), values=values, flags=flags)

    def formula(self, formula_id: str, version: str, inputs: dict[str, Any],
                output: Any) -> None:
        self.log("formula", formula_id=formula_id, version=version,
                 inputs=inputs, output=str(output))

    def retrieval(self, query: str, chunk_ids: list[str], pdf_hash: str) -> None:
        self.log("retrieval", query=query, chunk_ids=chunk_ids, pdf_hash=pdf_hash)

    def llm(self, prompt_version: str, prompt: str, response: str,
            validated: bool) -> None:
        self.log("llm", prompt_version=prompt_version, prompt=prompt,
                 response=response, validated=validated)

    def verification(self, name: str, outcome: str, reason: str) -> None:
        self.log("verification", name=name, outcome=outcome, reason=reason)
