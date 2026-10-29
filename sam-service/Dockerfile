# SG-Verify tracking service: SAM3 video tracker (tested on RTX 3060, NVIDIA driver 535 / CUDA 12.2).
# PyTorch 2.7 (recommended by SAM3) built for CUDA 12.6 runs with any CUDA 12 driver.
# Before building: git clone https://github.com/facebookresearch/sam3 sam-service/sam3
# Weights are not part of the image: mount sam3.pt at /models/sam3.pt (see README).
FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY sam3 /opt/sam3
RUN pip install --no-cache-dir -e /opt/sam3 flask pillow "numpy<2" opencv-python-headless scipy einops psutil \
        hydra-core pycocotools
# check that the model and the tracker can be built (without weights): a missing package shows up here,
# not on the first click
RUN python -c "from sam3.model_builder import build_sam3_video_model; from sam3.model.sam3_tracking_predictor import Sam3TrackerPredictor; print('sam3 import ok')"
WORKDIR /srv
COPY server.py /srv/server.py
EXPOSE 8000
CMD ["python", "/srv/server.py"]
