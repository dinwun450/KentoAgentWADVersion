# Steps to Run this:
# 1. In Terminal One:
npm run dev

# 2. In Terminal Two:
temporal server start-dev

# 3. In Terminal Three:
uv run kentoagent-temporal-worker

# 4. In Terminal Four:
uv run --env-file .env uvicorn kentoagent.api.app:app --reload