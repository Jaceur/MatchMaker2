# Matchmaker 2.0 API — container image for Railway.
#
# Deliberately installs ONLY api/requirements.txt (FastAPI + Starlette 0.41, no
# Streamlit) so it can't collide with the worker's Streamlit/Starlette-1.x deps.
# The whole repo is still copied in, so the API imports the shared project-root
# modules (scoring, leads, pipeline, directors, settings, leaderboard) directly.
#
# Only used by services that deploy from the react-rebuild branch — the worker on
# main keeps using Nixpacks + the root requirements.txt, untouched.

FROM python:3.12-slim

WORKDIR /app

# Install the isolated API dependency set first (better layer caching).
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# Copy the rest of the repo (shared modules + the api/ package).
COPY . .

# Run from the repo root so `import scoring`, `import leads`, etc. resolve.
# Railway injects $PORT; default to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
