"""Fail-closed runtime configuration for the Hypha administration broker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class BrokerConfigurationError(RuntimeError):
    """A generic startup failure that does not disclose configuration values."""

    def __init__(self) -> None:
        super().__init__("Hypha administration broker configuration is invalid")


@dataclass(frozen=True, repr=False)
class BrokerConfiguration:
    secret_verifier: str
    service_user_id: str
    service_password: str
    synapse_origin: str = "http://matrix-synapse:8008"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "BrokerConfiguration":
        required = {
            "HYPHA_ADMIN_BROKER_SECRET_VERIFIER",
            "HYPHA_ADMIN_BROKER_SERVICE_USER_ID",
            "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD",
        }
        if any(name not in environment for name in required):
            raise BrokerConfigurationError()
        values = {name: environment[name] for name in required}
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise BrokerConfigurationError()
        return cls(
            secret_verifier=values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"],
            service_user_id=values["HYPHA_ADMIN_BROKER_SERVICE_USER_ID"],
            service_password=values["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"],
        )
