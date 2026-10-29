# SG-Verify tracking service (SAM3)

A small Flask service that runs the [SAM3](https://github.com/facebookresearch/sam3) video tracker for the
*Track*, *Masks from boxes* and *Refine* buttons of the SG-Verify panel. It is the `sg_tools` service of
`deploy/docker-compose.yml` (profile `gpu`); traefik routes `/sg-tools/...` to it.

Build (done by `deploy/install.sh --gpu`):

```bash
git clone https://github.com/facebookresearch/sam3 sam-service/sam3
docker build -t sg-verify/sam-service:v0.1 sam-service
```

Weights are not included: request access to [facebook/sam3](https://huggingface.co/facebook/sam3) on Hugging Face,
download `sam3.pt` and put it into `deploy/models/` (mounted at `/models`). Frames are read from
`deploy/packages/<task name>/frames.zip` (mounted at `/packages`); `tools/upload_episodes.py --packages-dir` fills it.

Settings (environment): `SAM3_CKPT` (default `/models/sam3.pt`), `PACKAGES` (default `/packages`, several roots
separated by `:`), `SAM3_BF16` (`1`), `SAM3_MAX_STATES` (episodes kept on the GPU, `2`), `SG_GPU_MEMORY_GB` (cap of GPU
memory for the process when the GPU is shared).

API: see the docstring of `server.py` (`GET /sg-tools/health`, `POST /sg-tools/sam2/track`, `/sg-tools/sam2/from_box`,
`/sg-tools/sam3/refine`; `/sg-tools/sam3/...` are aliases of the `sam2` paths).
