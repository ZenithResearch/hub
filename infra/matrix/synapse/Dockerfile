FROM matrixdotorg/synapse@sha256:6882d26594b87171e0fe807ac6bd7f0000665cd70e73fb88c58ec9bff14c19ce

USER root

# The upstream 1.156.0 image predates patched cryptography wheels and the
# Twisted DNS decompression bound. Remove gosu after switching the final image
# to the image's established non-root Synapse UID/GID.
RUN python -m pip install --no-cache-dir --upgrade \
      "cryptography==48.0.1" \
      "Twisted==26.4.0rc2" \
    && rm -f /usr/sbin/gosu \
    && python -c "import cryptography, twisted, yaml, psycopg2" \
    && test -x /start.py

USER 991:991
