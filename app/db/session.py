from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def init_db(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=False,
        future=True,
        expire_on_commit=False,
    )
