"""The errors a client run can raise."""

from anony_mate_lib.models.tasks import TaskState


class AnonyMateError(Exception):
    """Base of the errors the client raises itself."""


class TaskFailedError(AnonyMateError):
    """A submitted job came back as ``failed``; its error text is the message."""


class MissingResourceError(AnonyMateError):
    """A finished task answered without the resource id its result is fetched under."""


def raise_for_task_failure(task: TaskState) -> None:
    """Raise `TaskFailedError` if the task failed; return otherwise."""
    if task.status == "failed":
        raise TaskFailedError(task.error or "the task failed without an error message")
