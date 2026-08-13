from dcc_backend_common.usage_tracking import UsageTrackingService
from dependency_injector import containers, providers

from anony_mate_backend.utils.app_config import AppConfig


class Container(containers.DeclarativeContainer):
    app_config: providers.Object[AppConfig] = providers.Object(AppConfig.from_env())

    usage_tracking_service: providers.Singleton[UsageTrackingService] = providers.Singleton(
        UsageTrackingService,
        hmac_secret=app_config.provided.hmac_secret,
    )
