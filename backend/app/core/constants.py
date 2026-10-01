"""Job status vocabulary shared across API, workers and UI."""

UPLOADED = "Uploaded"
MAPPING = "Mapping"
CLEANING = "Cleaning"
INDEXING = "Indexing"
MATCHING = "Matching"
COMPLETED = "Completed"
FAILED = "Failed"
PENDING_CONFIRMATION = "PendingConfirmation"

#: ordered pipeline phases -> share of overall progress bar
PHASE_PROGRESS: dict[str, float] = {
    UPLOADED: 0.02,
    MAPPING: 0.10,
    PENDING_CONFIRMATION: 0.15,
    CLEANING: 0.55,
    INDEXING: 0.80,
    MATCHING: 0.95,
    COMPLETED: 1.0,
    FAILED: 1.0,
}

ACTIVE_STATUSES = (UPLOADED, MAPPING, CLEANING, INDEXING, MATCHING, PENDING_CONFIRMATION)

#: which redis queue each phase should be dispatched to
PHASE_QUEUE: dict[str, str] = {
    CLEANING: "ingest",
    INDEXING: "ingest",
    MATCHING: "resolve",
}


def is_terminal(status: str) -> bool:
    return status in (COMPLETED, FAILED)
