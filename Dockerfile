FROM python:3.13-slim

WORKDIR /app

# Install Poetry
RUN pip install poetry

# Copy only dependency files first for better build caching
COPY pyproject.toml poetry.lock /app/

# Disable virtualenv creation — in Docker we install directly into the system Python
RUN poetry config virtualenvs.create false

# Install dependencies (no dev dependencies for production).
# Poetry defaults to zero download retries, so one dropped connection to PyPI
# fails the whole build (seen on PR #412: `Connection reset by peer` mid-wheel).
# Retry requests inside Poetry, and the install itself up to three times; the
# last attempt's exit status is the step's, so a real resolution error still fails.
ENV POETRY_REQUESTS_MAX_RETRIES=5
RUN for attempt in 1 2 3; do \
        poetry install --no-root --without dev && exit 0; \
        echo "poetry install attempt $attempt failed; retrying" >&2; \
        sleep 10; \
    done; \
    exit 1

# Copy the rest of the code
COPY . /app

# Set environment variables (optional)
ENV PYTHONUNBUFFERED=1

# No default CMD; user must specify the command when running the container
