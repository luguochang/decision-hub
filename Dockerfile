# Docker Hub is not reliably reachable from the supported local deployment
# environment. Use the official Docker Library mirror on Public ECR and pin the
# immutable multi-architecture digest; callers may explicitly override it with
# an equivalent internal mirror.
ARG NODE_IMAGE=public.ecr.aws/docker/library/node@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58
FROM ${NODE_IMAGE} AS web

WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${NPM_REGISTRY} \
    NPM_CONFIG_REGISTRY=${NPM_REGISTRY}
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/decision-desk/package.json apps/decision-desk/package.json
COPY packages/contracts_ts/package.json packages/contracts_ts/package.json
RUN --mount=type=cache,id=decision-hub-pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY apps/decision-desk apps/decision-desk
COPY packages/contracts_ts packages/contracts_ts
RUN pnpm --dir apps/decision-desk build

FROM ${UV_IMAGE}

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DECISION_HUB_DATA_DIR=/app/data/decision-hub
COPY pyproject.toml uv.lock ./
# Ship the audited DSH adapter dependency in the image. Runtime selection still
# defaults to replay in Compose; explicit DSH/provider use remains opt-in.
RUN uv sync --frozen --no-dev --no-install-project --extra dsh
COPY . .
COPY --from=web /app/apps/decision-desk/dist apps/decision-desk/dist
EXPOSE 8000
CMD ["uvicorn", "apps.hub_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
