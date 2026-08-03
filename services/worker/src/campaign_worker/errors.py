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
