import asyncio
from sqlalchemy import text

from app.db.session import init_db
from app.utils.schema_tools import (
    build_table_ref,
    compile_column_type,
    normalize_default,
    read_schema_info,
    reflect_table_create_sql,
    quote_name,
)

class DatabaseSynchronizer:
    def __init__(self, prod_db_url: str, dev_db_url: str):
        self.prod_db_url = prod_db_url
        self.dev_db_url = dev_db_url
        self.prod_db_engine = None
        self.dev_db_engine = None
        self.is_init = False

    async def init_db2(self):
        self.prod_db_engine = init_db(self.prod_db_url)
        self.dev_db_engine = init_db(self.dev_db_url)
        self.is_init = True

    async def analyze(self, schema: str = "public") -> dict:
        if not self.is_init:
            await self.init_db2()

        async with self.dev_db_engine.connect() as dev_conn, self.prod_db_engine.connect() as prod_conn:
            dev_info = await dev_conn.run_sync(read_schema_info, schema)
            prod_info = await prod_conn.run_sync(read_schema_info, schema)

            dev_tables = set(dev_info["tables"])
            prod_tables = set(prod_info["tables"])

            missing_tables = sorted(dev_tables - prod_tables)
            extra_tables = sorted(prod_tables - dev_tables)
            common_tables = sorted(dev_tables & prod_tables)

            plan = {
                "schema": schema,
                "missing_tables": missing_tables,
                "extra_tables": extra_tables,
                "missing_columns": [],
                "changed_columns": [],
                "skipped_changes": [],
                "warnings": [],
                "sql_statements": [],
                "summary": {},
            }

            for table_name in missing_tables:
                create_table_sql = await dev_conn.run_sync(reflect_table_create_sql, table_name, schema)
                plan["sql_statements"].append(create_table_sql)

            for table_name in common_tables:
                dev_columns = {
                    col["name"]: col
                    for col in dev_info["columns"].get(table_name, [])
                }
                prod_columns = {
                    col["name"]: col
                    for col in prod_info["columns"].get(table_name, [])
                }

                missing_columns = sorted(set(dev_columns) - set(prod_columns))
                common_columns = sorted(set(dev_columns) & set(prod_columns))

                for column_name in missing_columns:
                    col = dev_columns[column_name]
                    col_type_sql = compile_column_type(col["type"])
                    nullable = col.get("nullable", True)
                    default = normalize_default(col.get("default"))

                    if not nullable and default is None:
                        plan["skipped_changes"].append(
                            {
                                "table": table_name,
                                "column": column_name,
                                "reason": "NOT NULL column without default may fail on non-empty production table",
                            }
                        )
                        plan["warnings"].append(
                            f"Skipped column {table_name}.{column_name}: NOT NULL without default"
                        )
                        continue

                    sql_parts = [
                        f"ALTER TABLE {build_table_ref(table_name, schema)} "
                        f"ADD COLUMN {quote_name(column_name)} {col_type_sql}"
                    ]

                    if default is not None:
                        sql_parts.append(f"DEFAULT {default}")

                    if nullable is False:
                        sql_parts.append("NOT NULL")

                    add_column_sql = " ".join(sql_parts) + ";"

                    plan["missing_columns"].append(
                        {
                            "table": table_name,
                            "column": column_name,
                            "type": col_type_sql,
                            "nullable": nullable,
                            "default": default,
                        }
                    )
                    plan["sql_statements"].append(add_column_sql)

                for column_name in common_columns:
                    dev_col = dev_columns[column_name]
                    prod_col = prod_columns[column_name]

                    dev_type = compile_column_type(dev_col["type"])
                    prod_type = compile_column_type(prod_col["type"])

                    dev_nullable = dev_col.get("nullable", True)
                    prod_nullable = prod_col.get("nullable", True)

                    dev_default = normalize_default(dev_col.get("default"))
                    prod_default = normalize_default(prod_col.get("default"))

                    if (
                            dev_type != prod_type
                            or dev_nullable != prod_nullable
                            or dev_default != prod_default
                    ):
                        plan["changed_columns"].append(
                            {
                                "table": table_name,
                                "column": column_name,
                                "dev": {
                                    "type": dev_type,
                                    "nullable": dev_nullable,
                                    "default": dev_default,
                                },
                                "prod": {
                                    "type": prod_type,
                                    "nullable": prod_nullable,
                                    "default": prod_default,
                                },
                            }
                        )

            for table_name in extra_tables:
                plan["warnings"].append(
                    f"Table exists only in production and will not be removed automatically: {table_name}"
                )

            for item in plan["changed_columns"]:
                plan["warnings"].append(
                    f"Column differs but will not be changed automatically: "
                    f"{item['table']}.{item['column']}"
                )

            plan["summary"] = {
                "missing_tables_count": len(plan["missing_tables"]),
                "extra_tables_count": len(plan["extra_tables"]),
                "missing_columns_count": len(plan["missing_columns"]),
                "changed_columns_count": len(plan["changed_columns"]),
                "skipped_changes_count": len(plan["skipped_changes"]),
                "sql_statements_count": len(plan["sql_statements"]),
                "warnings_count": len(plan["warnings"]),
            }

            return plan

    async def execute(self, plan: dict) -> dict:
        if not self.is_init:
            await self.init_db2()

        if not isinstance(plan, dict):
            raise ValueError("Plan must be a dictionary")

        if not plan:
            raise ValueError("Plan is empty")

        schema = plan.get("schema", "public")

        required_keys = {
            "missing_tables",
            "missing_columns",
            "changed_columns",
            "skipped_changes",
            "warnings",
        }
        missing_keys = required_keys - set(plan.keys())
        if missing_keys:
            raise ValueError(f"Plan is missing required keys: {', '.join(sorted(missing_keys))}")

        result = {
            "success": False,
            "schema": schema,
            "executed_statements": [],
            "skipped_statements": [],
            "warnings": list(plan.get("warnings", [])),
            "errors": [],
            "summary": {},
        }

        async with self.prod_db_engine.connect() as prod_conn:
            current_prod_info = await prod_conn.run_sync(read_schema_info, schema)

        current_tables = set(current_prod_info["tables"])

        tables_to_create = []
        for table_name in plan.get("missing_tables", []):
            if table_name not in current_tables:
                tables_to_create.append(table_name)
            else:
                result["skipped_statements"].append(
                    {
                        "type": "create_table",
                        "table": table_name,
                        "reason": "table already exists in production",
                    }
                )

        columns_to_add = []
        for item in plan.get("missing_columns", []):
            table_name = item["table"]
            column_name = item["column"]

            if table_name not in current_tables:
                result["skipped_statements"].append(
                    {
                        "type": "add_column",
                        "table": table_name,
                        "column": column_name,
                        "reason": "table does not exist yet, column will be created with table creation",
                    }
                )
                continue

            prod_columns = {
                col["name"]: col
                for col in current_prod_info["columns"].get(table_name, [])
            }

            if column_name in prod_columns:
                result["skipped_statements"].append(
                    {
                        "type": "add_column",
                        "table": table_name,
                        "column": column_name,
                        "reason": "column already exists in production",
                    }
                )
                continue

            columns_to_add.append(item)

        if not tables_to_create and not columns_to_add:
            result["success"] = True
            result["summary"] = {
                "tables_created": 0,
                "columns_added": 0,
                "skipped_count": len(result["skipped_statements"]),
                "warnings_count": len(result["warnings"]),
                "errors_count": 0,
            }
            return result

        try:
            async with self.prod_db_engine.begin() as conn:
                for table_name in tables_to_create:
                    async with self.dev_db_engine.connect() as dev_conn:
                        create_table_sql = await dev_conn.run_sync(
                            reflect_table_create_sql,
                            table_name,
                            schema,
                        )

                    await conn.execute(text(create_table_sql))
                    result["executed_statements"].append(
                        {
                            "type": "create_table",
                            "table": table_name,
                            "sql": create_table_sql,
                        }
                    )

                created_tables = set(tables_to_create)

                for item in columns_to_add:
                    table_name = item["table"]
                    column_name = item["column"]
                    column_type = item["type"]
                    nullable = item.get("nullable", True)
                    default = item.get("default")

                    if table_name in created_tables:
                        result["skipped_statements"].append(
                            {
                                "type": "add_column",
                                "table": table_name,
                                "column": column_name,
                                "reason": "table was created in this execution, column already included in CREATE TABLE",
                            }
                        )
                        continue

                    sql_parts = [
                        f"ALTER TABLE {build_table_ref(table_name, schema)} "
                        f"ADD COLUMN {quote_name(column_name)} {column_type}"
                    ]

                    if default is not None:
                        sql_parts.append(f"DEFAULT {default}")

                    if nullable is False:
                        sql_parts.append("NOT NULL")

                    add_column_sql = " ".join(sql_parts) + ";"

                    await conn.execute(text(add_column_sql))
                    result["executed_statements"].append(
                        {
                            "type": "add_column",
                            "table": table_name,
                            "column": column_name,
                            "sql": add_column_sql,
                        }
                    )

            if plan.get("changed_columns"):
                for item in plan["changed_columns"]:
                    result["warnings"].append(
                        f"Column was not changed automatically: {item['table']}.{item['column']}"
                    )

            if plan.get("skipped_changes"):
                for item in plan["skipped_changes"]:
                    result["warnings"].append(
                        f"Skipped unsafe change: {item['table']}.{item['column']} - {item['reason']}"
                    )

            result["success"] = True
            result["summary"] = {
                "tables_created": len(
                    [s for s in result["executed_statements"] if s["type"] == "create_table"]
                ),
                "columns_added": len(
                    [s for s in result["executed_statements"] if s["type"] == "add_column"]
                ),
                "skipped_count": len(result["skipped_statements"]),
                "warnings_count": len(result["warnings"]),
                "errors_count": 0,
            }
            return result

        except Exception as e:
            result["errors"].append(str(e))
            result["success"] = False
            result["summary"] = {
                "tables_created": len(
                    [s for s in result["executed_statements"] if s["type"] == "create_table"]
                ),
                "columns_added": len(
                    [s for s in result["executed_statements"] if s["type"] == "add_column"]
                ),
                "skipped_count": len(result["skipped_statements"]),
                "warnings_count": len(result["warnings"]),
                "errors_count": len(result["errors"]),
            }
            return result

