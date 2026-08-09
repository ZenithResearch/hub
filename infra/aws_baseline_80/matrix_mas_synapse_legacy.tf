locals {
  # Byte-equivalent legacy Synapse bootstrap used unless the reviewed MAS
  # migration has completed. Keeping this branch unchanged prevents phase-one
  # MAS infrastructure from registering a new production Synapse revision.
  matrix_synapse_legacy_command = <<-SCRIPT
    umask 077
    python - <<'PY'
    import os
    import hashlib
    import urllib.request
    from pathlib import Path
    import psycopg2
    import yaml

    server_name = os.environ["SYNAPSE_SERVER_NAME"]
    data_dir = Path("/data")
    ca_path = data_dir / "aws-rds-global-bundle.pem"
    ca_bytes = urllib.request.urlopen(os.environ["SYNAPSE_RDS_CA_BUNDLE_URL"], timeout=30).read()
    ca_digest = hashlib.sha256(ca_bytes).hexdigest()
    if ca_digest != os.environ["SYNAPSE_RDS_CA_BUNDLE_SHA256"]:
        raise SystemExit("RDS CA bundle checksum mismatch")
    ca_path.write_bytes(ca_bytes)

    database_connect = {
        "user": "synapse",
        "password": os.environ["SYNAPSE_DB_PASSWORD"],
        "host": os.environ["SYNAPSE_DB_HOST"],
        "port": 5432,
        "sslmode": "verify-full",
        "sslrootcert": str(ca_path),
        "connect_timeout": 15,
        "options": "-c statement_timeout=30000",
    }
    with psycopg2.connect(database="postgres", **database_connect) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT datcollate, datctype FROM pg_database WHERE datname = 'synapse'")
            locale = cursor.fetchone()
        if locale is None:
            with admin.cursor() as cursor:
                cursor.execute("CREATE DATABASE synapse WITH TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")
        elif locale != ("C", "C"):
            raise SystemExit("existing Synapse database has an unsafe non-C locale; explicit migration is required")

    signing_key_path = data_dir / f"{server_name}.signing.key"
    signing_key_path.write_text(os.environ["SYNAPSE_SIGNING_KEY"].rstrip("\n") + "\n")

    homeserver = {
        "server_name": server_name,
        "public_baseurl": f"https://{server_name}/",
        "pid_file": "/data/homeserver.pid",
        "listeners": [{
            "port": 8008,
            "tls": False,
            "type": "http",
            "x_forwarded": True,
            "bind_addresses": ["0.0.0.0"],
            "resources": [{"names": ["client", "federation"], "compress": False}],
        }],
        "database": {
            "name": "psycopg2",
            "args": {
                "user": "synapse",
                "password": os.environ["SYNAPSE_DB_PASSWORD"],
                "database": "synapse",
                "host": os.environ["SYNAPSE_DB_HOST"],
                "port": 5432,
                "sslmode": "verify-full",
                "sslrootcert": "/data/aws-rds-global-bundle.pem",
                "cp_min": 1,
                "cp_max": 5,
            },
        },
        "log_config": "/data/log.config",
        "media_store_path": "/data/media_store",
        "signing_key_path": str(signing_key_path),
        "macaroon_secret_key": os.environ["SYNAPSE_MACAROON_SECRET_KEY"],
        "registration_shared_secret": os.environ["SYNAPSE_REGISTRATION_SHARED_SECRET"],
        "form_secret": os.environ["SYNAPSE_FORM_SECRET"],
        "enable_registration": False,
        "report_stats": False,
        "suppress_key_server_warning": True,
        "trusted_key_servers": [{"server_name": "matrix.org"}],
    }
    (data_dir / "homeserver.yaml").write_text(yaml.safe_dump(homeserver, sort_keys=False))

    log_config = {
        "version": 1,
        "formatters": {"precise": {"format": "%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "precise"}},
        "root": {"level": "INFO", "handlers": ["console"]},
        "disable_existing_loggers": False,
    }
    (data_dir / "log.config").write_text(yaml.safe_dump(log_config, sort_keys=False))
    PY
    exec /start.py run
  SCRIPT
}
