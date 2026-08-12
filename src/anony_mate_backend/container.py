from dependency_injector import containers, providers

from anony_mate_backend.utils.configuration import Configuration


class Container(containers.DeclarativeContainer):
    config: providers.Object[Configuration] = providers.Object(Configuration.from_env())
