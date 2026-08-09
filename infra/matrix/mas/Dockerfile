FROM ghcr.io/element-hq/matrix-authentication-service@sha256:8cb319ec41706adc1ed8b5b63e1de2f067073cc33a304686226f658f5e83c8b3

USER root
ADD --checksum=sha256:e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3 \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /etc/ssl/certs/aws-rds-global-bundle.pem
COPY --chmod=0555 infra/matrix/mas/entrypoint.sh /usr/local/bin/zenith-mas-entrypoint
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/zenith-mas-entrypoint"]
