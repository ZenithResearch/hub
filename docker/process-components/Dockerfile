FROM __BASE__ AS installer
ARG SOURCE_DATE_EPOCH=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PIP_NO_INDEX=1 PIP_CONFIG_FILE=/dev/null
COPY wheelhouse/ /wheels/
COPY requirements.lock build-inputs.json /opt/evidence/
COPY inventory.py /opt/image-tools/inventory.py
RUN --network=none python -m venv /opt/component && \
    /opt/component/bin/python -m pip install --no-index --find-links=/wheels --require-hashes --no-compile --no-cache-dir --disable-pip-version-check -r /opt/evidence/requirements.lock && \
    /opt/component/bin/python -m pip check && \
    dpkg-query -W -f='${Package}\t${Version}\n' > /opt/evidence/base-packages.txt && \
    /opt/component/bin/python -I -B /opt/image-tools/inventory.py --verify-build /opt/evidence/build-inputs.json --output /opt/evidence/installed.json

FROM __BASE__
ARG SOURCE_DATE_EPOCH=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PIP_NO_INDEX=1 PIP_CONFIG_FILE=/dev/null PATH=/opt/component/bin:/usr/local/bin:/usr/bin:/bin
COPY --from=installer /opt/component /opt/component
COPY --from=installer /opt/evidence /opt/evidence
COPY inventory.py smoke.py /opt/image-tools/
RUN --network=none mkdir /data && chown 10001:10001 /data && chmod 0700 /data
LABEL org.opencontainers.image.title="__COMPONENT__" org.opencontainers.image.revision="__COMMIT__" org.opencontainers.image.version="0.1.0" io.hub.production-qualified="false" io.hub.native-helpers="absent"
USER 10001:10001
WORKDIR /data
ENTRYPOINT __ENTRYPOINT__
CMD ["--help"]
