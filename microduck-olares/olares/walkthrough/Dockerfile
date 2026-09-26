# The walkthrough pipeline, for the GPU box (Olares). Needs an NVIDIA GPU.
#   docker build -t walkthrough .
#   docker run --rm --gpus all -v /path/to/walkthrough:/data walkthrough /data
FROM ghcr.io/nerfstudio-project/nerfstudio:latest

# AprilTag detection (cv2.aruco) and TOML on older Pythons.
RUN pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python || true \
 && pip install --no-cache-dir opencv-contrib-python-headless "tomli; python_version < '3.11'"
# The person filter's weights, fetched once here so a run needs no internet at all.
RUN python -c "import torchvision as t; t.models.detection.fasterrcnn_mobilenet_v3_large_fpn(weights='DEFAULT')"

COPY walkthrough /app/walkthrough
WORKDIR /app
ENTRYPOINT ["python", "-m", "walkthrough"]
