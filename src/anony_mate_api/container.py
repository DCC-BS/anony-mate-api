from dcc_backend_common.usage_tracking import UsageTrackingService
from dependency_injector import containers, providers

from anony_mate_api.services.document_converstion_service import DocumentConversionService
from anony_mate_api.services.redact_service import RedactService
from anony_mate_api.services.task_store import LaneConfig, TaskStore
from anony_mate_api.utils.app_config import AppConfig


class Container(containers.DeclarativeContainer):
    app_config: providers.Object[AppConfig] = providers.Object(AppConfig.from_env())

    usage_tracking_service: providers.Singleton[UsageTrackingService] = providers.Singleton(
        UsageTrackingService,
        hmac_secret=app_config.provided.hmac_secret,
    )

    redact_service: providers.Singleton[RedactService] = providers.Singleton(RedactService, config=app_config)
    # One store for both conversions and redactions: the task and resource
    # endpoints are shared, so the ids have to come from the same place.
    # Each lane is sized to what its downstream service can actually run, so
    # the backlog waits here — visible and fairly ordered — instead of in a
    # queue belonging to a service this API cannot see into or steer.
    task_store: providers.Singleton[TaskStore] = providers.Singleton(
        TaskStore,
        ttl_seconds=app_config.provided.task_result_ttl_seconds,
        abandoned_after_seconds=app_config.provided.task_abandoned_after_seconds,
        lanes=providers.Dict(
            convert=providers.Factory(
                LaneConfig,
                workers=app_config.provided.conversion_max_concurrent,
                max_queued=app_config.provided.conversion_max_queued,
            ),
            redact=providers.Factory(
                LaneConfig,
                workers=app_config.provided.redaction_max_concurrent,
                max_queued=app_config.provided.redaction_max_queued,
            ),
        ),
    )
    document_conversion_service: providers.Singleton[DocumentConversionService] = providers.Singleton(
        DocumentConversionService, config=app_config
    )
