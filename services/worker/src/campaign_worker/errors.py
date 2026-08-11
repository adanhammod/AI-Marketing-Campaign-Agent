class WorkerError(Exception):
    pass


class ConfigurationError(WorkerError):
    pass


class PersistenceUnavailable(WorkerError):
    pass


class LeaseConflict(WorkerError):
    pass


class LeaseLost(WorkerError):
    pass


class ProcessingUncertain(WorkerError):
    pass


class WorkflowOperationError(WorkerError):
    """A safe, typed failure that can cross the graph error boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
