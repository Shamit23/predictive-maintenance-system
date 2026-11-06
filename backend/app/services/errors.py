"""Domain errors. Each carries an HTTP status and a stable machine-readable `code`.

The API turns these into a uniform JSON envelope; messages here must never contain
secrets, filesystem paths or stack traces.
"""
from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, status: int, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.status, self.message, self.details = code, status, message, details or {}


class NotFound(AppError):
    def __init__(self, what: str):
        super().__init__("not_found", 404, f"{what} not found.")


class ConflictingDuplicate(AppError):
    def __init__(self, engine_id: int, cycle: int):
        super().__init__(
            "conflicting_duplicate", 409,
            "A reading for this engine and cycle already exists with different values.",
            {"engine_id": engine_id, "cycle": cycle},
        )


class CycleGap(AppError):
    def __init__(self, engine_id: int, cycle: int, expected: int):
        super().__init__(
            "cycle_gap", 409,
            f"Cycle {cycle} is not the next expected cycle ({expected}) for engine {engine_id}. "
            "Readings must arrive in order without gaps.",
            {"engine_id": engine_id, "cycle": cycle, "expected_cycle": expected},
        )


class InsufficientHistory(AppError):
    def __init__(self, engine_id: int, have: int, need: int):
        super().__init__(
            "insufficient_history", 422,
            f"Engine {engine_id} has {have} reading(s) up to the requested cycle; at least {need} required.",
            {"engine_id": engine_id, "available": have, "required": need},
        )


class SimulationRunNotActive(AppError):
    def __init__(self):
        super().__init__("simulation_run_not_active", 409,
                         "The simulation run named by X-Simulation-Run does not exist or is not running.")


class EngineNotInRun(AppError):
    def __init__(self, engine_id: int):
        super().__init__("engine_not_in_run", 409,
                         f"Engine {engine_id} is not part of the simulation run named by X-Simulation-Run.",
                         {"engine_id": engine_id})


class EngineOriginMismatch(AppError):
    def __init__(self, engine_id: int, origin: str):
        what = ("was created by simulation and accepts only readings from a simulation run" if origin == "simulation"
                else "was created by ordinary ingestion and cannot receive simulated readings")
        super().__init__("engine_origin_mismatch", 409, f"Engine {engine_id} {what}.",
                         {"engine_id": engine_id, "origin": origin})


class ModelUnavailable(AppError):
    def __init__(self, message: str = "No model is loaded."):
        super().__init__("model_unavailable", 503, message)


class ModelStartupError(RuntimeError):
    """Raised at startup when the active model cannot be determined or loaded safely."""
