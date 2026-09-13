from __future__ import annotations

import argparse
import asyncio
import json

from kentoagent.evaluation.runner import evaluate_many
from kentoagent.evaluation.weave_eval import run_weave_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run repeated seeded KentoAgent evaluations")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument(
        "--weave",
        action="store_true",
        help="Publish a native Weave Evaluation with systemic scorers",
    )
    parser.add_argument("--policy-version", default="deterministic-v1")
    args = parser.parse_args()
    if args.weave:
        summary = asyncio.run(
            run_weave_evaluation(
                args.runs,
                args.seed,
                max_steps=args.max_steps,
                policy_version=args.policy_version,
            )
        )
        print(json.dumps(summary, indent=2, default=str))
        return
    results = evaluate_many(args.runs, args.seed, max_steps=args.max_steps)
    print(json.dumps([result.model_dump(mode="json") for result in results], indent=2))


if __name__ == "__main__":
    main()
