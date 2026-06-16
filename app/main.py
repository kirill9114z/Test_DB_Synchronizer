import os
import json
import argparse
import asyncio

from dotenv import load_dotenv

from services.db_synchronizer import DatabaseSynchronizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PostgreSQL schema synchronizer"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze differences between dev and prod databases"
    )
    analyze_parser.add_argument(
        "--schema",
        default=os.getenv("DB_SCHEMA", "public"),
        help="Database schema name (default: public or DB_SCHEMA from .env)"
    )

    execute_parser = subparsers.add_parser(
        "execute",
        help="Analyze and execute safe schema changes"
    )
    execute_parser.add_argument(
        "--schema",
        default=os.getenv("DB_SCHEMA", "public"),
        help="Database schema name (default: public or DB_SCHEMA from .env)"
    )

    return parser


async def run() -> None:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    dev_db_url = os.getenv("DEV_DB_URL")
    prod_db_url = os.getenv("PROD_DB_URL")

    if not dev_db_url:
        raise ValueError("DEV_DB_URL is not set")
    if not prod_db_url:
        raise ValueError("PROD_DB_URL is not set")

    synchronizer = DatabaseSynchronizer(
        prod_db_url=prod_db_url,
        dev_db_url=dev_db_url,
    )

    try:
        if args.command == "analyze":
            plan = await synchronizer.analyze(schema=args.schema)
            print(json.dumps(plan, indent=4, ensure_ascii=False))

        elif args.command == "execute":
            plan = await synchronizer.analyze(schema=args.schema)
            result = await synchronizer.execute(plan)
            print(json.dumps(result, indent=4, ensure_ascii=False))

    finally:
        if synchronizer.prod_db_engine is not None:
            await synchronizer.prod_db_engine.dispose()
        if synchronizer.dev_db_engine is not None:
            await synchronizer.dev_db_engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())