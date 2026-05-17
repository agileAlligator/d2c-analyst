#!/usr/bin/env python3
"""Run the Margin Watch agent for a merchant."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.margin_watch import MarginWatchAgent
from app.warehouse.db import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant", default="demo")
    args = parser.parse_args()

    with SessionLocal() as db:
        agent = MarginWatchAgent(db=db, merchant_id=args.merchant)
        run = agent.run()
        # Read all attributes inside the session before it closes
        run_id = str(run.id)
        run_status = run.status
        run_proposals = run.proposals or []
        run_log = run.log_md or ""

    print(f"\nRun ID: {run_id}")
    print(f"Status: {run_status}")
    print(f"Proposals: {len(run_proposals)}")
    print("\n" + "=" * 60)
    print(run_log)


if __name__ == "__main__":
    main()
