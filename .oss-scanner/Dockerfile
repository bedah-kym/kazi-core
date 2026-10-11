# Build for Anthropic OSS Scanner (https://github.com/anthropics/oss-scanner).
# Mirrors the hermetic-tests CI lane: SQLite + LocMem cache, no Postgres/Redis, no network after this build.
FROM python:3.12-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SECRET_KEY=oss-scanner-not-for-prod \
    DJANGO_DEBUG=true \
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    ENCRYPTION_KEY=I2m1pOTNatH-LkRdlnWXZXqZ9-8oQRm-JCwpwshx6dc=

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

RUN pip install -r requirements.lock

RUN python Backend/manage.py check

# Full suite, as in CI's hermetic lane. A failing test must not fail the image.
RUN python Backend/manage.py test --noinput || echo "WARNING: test suite reported failures"
