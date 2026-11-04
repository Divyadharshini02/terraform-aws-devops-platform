FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY autoscaler ./autoscaler

# Runs as root on purpose: the mounted Docker socket is owned by root:root
# with mode 0660, and a non-root user gets "Permission denied" (verified in
# the M7 audit). Compose adds read_only, no-new-privileges and cap_drop: ALL.
CMD ["python", "-m", "autoscaler.main"]
