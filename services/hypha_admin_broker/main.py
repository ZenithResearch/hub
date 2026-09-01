"""ASGI entrypoint for the Hypha administration broker."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI

from .api import create_app
from .auth import BrokerSessionStore
from .config import BrokerConfiguration
from .secret_store import AtomicFileSecretVerifierStore
from .synapse import SynapseAdminAdapterClient


def create_runtime_app(environment: Mapping[str, str]) -> FastAPI:
    configuration = BrokerConfiguration.from_environment(environment)
    verifier_store = None
    verifier = configuration.secret_verifier
    if configuration.secret_verifier_path is not None:
        verifier_store = AtomicFileSecretVerifierStore(configuration.secret_verifier_path)
        verifier = verifier_store.load_or_initialize(verifier)
    sessions = BrokerSessionStore(verifier=verifier)
    synapse = SynapseAdminAdapterClient(
        homeserver=configuration.synapse_origin,
        service_user_id=configuration.service_user_id,
        service_password=configuration.service_password,  # private-artifact-scan: allow-variable-flow
    )
    return create_app(
        session_store=sessions,
        synapse=synapse,
        secret_verifier_store=verifier_store,
    )


def runtime_app() -> FastAPI:
    import os

    return create_runtime_app(os.environ)
