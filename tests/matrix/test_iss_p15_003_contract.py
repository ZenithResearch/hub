"""Contract test guard for ISS-P15-003: homeserver config must target production outside local-dev.

This test is intentionally failing until the contract implementation lands.
"""

import pytest

def test_iss_p15_003_production_homeserver_contract():
    # TODO(ISS-P15-003): implement config loader + env precedence
    # Expected: outside explicit local-dev, homeserver_url == "https://synapse.zenith-research.ca"
    # and server_name preserved separately.
    pytest.fail("ISS-P15-003 contract not yet implemented: no homeserver config loader enforcing production target")
