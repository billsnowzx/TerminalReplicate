from __future__ import annotations

import argparse
import json

from macro_platform.services.platform import PlatformService


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap and refresh V1 workspace data.")
    parser.add_argument(
        "--run-macro-brief-job",
        action="store_true",
        help="Run v1-macro-brief-job during refresh.",
    )
    args = parser.parse_args()
    service = PlatformService()
    result = service.refresh_v1_workspace(run_macro_brief_job=args.run_macro_brief_job)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
