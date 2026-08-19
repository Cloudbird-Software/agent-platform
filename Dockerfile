# agent-platform 执行镜像（ADR-0025）——clone 后 docker build 即用。
#
# 双锁面（ADR-0025）：
#   核心锁面 uv.lock        —— spec/render/flow/governance/drift/observe（零上游）
#   运行锁面 runtime/requirements.lock —— openjiuwen==0.1.16 / jiuwenswarm==0.2.3
#                                     （独立审计面：上游树含未修高危，显式风险准入）

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS core
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && uv build

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
WORKDIR /app
# 运行时锁面（哈希钉版——与 uv.lock 分离的供应链边界）
COPY runtime/requirements.lock ./runtime/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r runtime/requirements.lock
COPY --from=core /app/dist/*.whl ./
RUN pip install --no-cache-dir ./*.whl && rm ./*.whl
# vendor 声明快照（默认声明源——容器内 ap init 零参数）
COPY vendor ./vendor
# workspace 渲染产物与治理 state 挂载点（声明投影可重建；账本 append-only 不可清洗）
VOLUME ["/app/workspace"]
ENV PYTHONUNBUFFERED=1
# 启动即自检：渲染面/凭据/账本/runtime 逐面报告（fail 退出非 0）
ENTRYPOINT ["ap"]
CMD ["doctor", "--registry", "/app/vendor/agent-registry", "--workspace", "/app/workspace"]
