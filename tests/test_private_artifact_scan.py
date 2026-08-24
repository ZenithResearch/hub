from scripts import private_artifact_scan


def test_reviewed_variable_flow_marker_allows_only_unquoted_python_source_flow():
    assert private_artifact_scan.benign_content_line(
        "services/example.py",
        b"access_token=access_token,  # private-artifact-scan: allow-variable-flow",
    )
    assert not private_artifact_scan.benign_content_line(
        "services/example.py",
        b'access_token="literal-secret-value"  # private-artifact-scan: allow-variable-flow',
    )
    assert not private_artifact_scan.benign_content_line(
        "tests/example.py",
        b"access_token=access_token  # private-artifact-scan: allow-variable-flow",
    )


def test_reviewed_fixture_marker_is_limited_to_python_tests():
    line = b'ACCESS_TOKEN="test-only-value"  # private-artifact-scan: allow-test-fixture'

    assert private_artifact_scan.benign_content_line("tests/example.py", line)
    assert not private_artifact_scan.benign_content_line("services/example.py", line)
    assert not private_artifact_scan.benign_content_line("tests/example.yaml", line)
