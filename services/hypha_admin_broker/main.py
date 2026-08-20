"""ASGI entrypoint for the Hypha administration broker."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI

from .api import create_app
from .auth import BrokerSessionStore
from .config import BrokerConfiguration
from .synapse import SynapseAdminAdapterClient


def create_runtime_app(environment: Mapping[str, str]) -> FastAPI:
    configuration = BrokerConfiguration.from_environment(environment)
    sessions = BrokerSessionStore(verifier=configuration.secret_verifier)
    synapse = SynapseAdminAdapterClient(
        homeserver=configuration.synapse_origin,
        service_user_id=configuration.service_user_id,
        service_password=configuration.service_password,
    )
    return create_app(session_store=sessions, synapse=synapse)


def runtime_app() -> FastAPI:
    import os

    return create_runtime_app(os.environ)
