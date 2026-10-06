# syntax=docker/dockerfile:1
# 分析サービス（API）と画面（UI）で、同じイメージを使う。起動のコマンドだけが違う（docker-compose.yml）。
# イメージに、秘密情報（API キー・DB のパスワード）を入れない。実行時に、環境変数で渡す。

FROM python:3.13-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.33
WORKDIR /app
# 依存関係だけを先に入れる（ソースを変えても、この層は再利用される）。uv.lock のとおりに入れる
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CREWAI_DISABLE_TELEMETRY=true
# root では動かさない。書き込める場所は、/app/data（Trace と Memory。ボリュームを割り当てる）と、ホーム（CrewAI が import 時に作業用のフォルダーを作る。捨ててよい）
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app && mkdir -p /app/data && chown app:app /app/data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY shared ./shared
COPY chapters/ch14-fastapi-service ./chapters/ch14-fastapi-service
COPY chapters/ch15-docker-postgresql ./chapters/ch15-docker-postgresql
COPY data/ai_agent_practice_sales.csv ./data/ai_agent_practice_sales.csv
USER app
EXPOSE 8000
# プロセスが生きているか（DB には触れない）。DB に届くか（受付の可否）は /readyz。
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "--factory", "shared.api:create_app", "--host", "0.0.0.0", "--port", "8000"]
