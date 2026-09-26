# The Nest's core (ducks, spine, memory, bus, the Pond) for the Jetson Orin Nano Super (arm64).
#
#   docker build -t nest .
#   docker run -d --name nest --network host --restart unless-stopped \
#     -v /var/lib/nest:/var/lib/nest -v $PWD/config.toml:/etc/nest/config.toml:ro nest
#
# Reachy's camera ("room eye", Phase 2) needs the reachy_mini SDK and GStreamer next to Reachy's
# daemon, so run the Nest on the host instead when you turn it on (see nest.service).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY nest ./nest
RUN pip install --no-cache-dir .

# --network host: the ducks' WebRTC (UDP) and Reachy's daemon on localhost:8000 both need it.
EXPOSE 8090
CMD ["python", "-m", "nest", "--config", "/etc/nest/config.toml"]
