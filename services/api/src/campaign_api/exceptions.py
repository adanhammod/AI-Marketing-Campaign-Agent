class CampaignAPIError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500
    retryable = False


class CampaignNotFound(CampaignAPIError):
    code = "NOT_FOUND"
    status_code = 404


class DuplicateCampaign(CampaignAPIError):
    code = "STATE_CONFLICT"
    status_code = 409


class InvalidStateTransition(CampaignAPIError):
    code = "STATE_CONFLICT"
    status_code = 409


class RepositoryFailure(CampaignAPIError):
    code = "STORAGE_UNAVAILABLE"
    status_code = 503
    retryable = True


class QueueSubmissionFailure(CampaignAPIError):
    code = "QUEUE_UNAVAILABLE"
    status_code = 503
    retryable = True


class QueueSubmissionAmbiguousFailure(QueueSubmissionFailure):
    pass


class DuplicateJobConflict(CampaignAPIError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409


class InvalidCursor(CampaignAPIError):
    code = "VALIDATION_ERROR"
    status_code = 400
