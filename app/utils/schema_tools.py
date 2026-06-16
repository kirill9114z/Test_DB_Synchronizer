from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


dialect = postgresql.dialect()
preparer = dialect.identifier_preparer


def quote_name(name: str) -> str:
    return preparer.quote(name)


def build_table_ref(table_name: str, schema: str = "public") -> str:
    if schema:
        return f"{quote_name(schema)}.{quote_name(table_name)}"
    return quote_name(table_name)


def normalize_default(value):
    if value is None:
        return None
    return str(value).strip()


def compile_column_type(col_type) -> str:
    return str(col_type.compile(dialect=dialect))


def read_schema_info(sync_conn, schema: str = "public") -> dict:
    inspector = inspect(sync_conn)
    tables = inspector.get_table_names(schema=schema)

    columns_by_table = {}
    pk_by_table = {}
    indexes_by_table = {}

    for table_name in tables:
        columns_by_table[table_name] = inspector.get_columns(table_name, schema=schema)
        pk_by_table[table_name] = inspector.get_pk_constraint(table_name, schema=schema)
        indexes_by_table[table_name] = inspector.get_indexes(table_name, schema=schema)

    return {
        "tables": sorted(tables),
        "columns": columns_by_table,
        "pks": pk_by_table,
        "indexes": indexes_by_table,
    }


def reflect_table_create_sql(sync_conn, table_name: str, schema: str = "public") -> str:
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        schema=schema,
        autoload_with=sync_conn,
    )
    sql = str(CreateTable(table).compile(dialect=dialect)).strip()
    return sql + ";"