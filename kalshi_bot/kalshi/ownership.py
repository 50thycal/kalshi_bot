"""Opt-in, shared primary-account market ownership. Claims never expire.

All participants must use the SAME database and namespace. This coordinates
software, not unrestricted credentials or manual exchange actions.
"""
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.exc import IntegrityError


class MarketOwnership:
    def __init__(self, url: str, namespace: str):
        if not url or not namespace:
            raise ValueError("shared ownership configuration required")
        if url.startswith(("postgres://", "postgresql://")):
            url = "postgresql+psycopg://" + url.split("://", 1)[1]
        self.engine = create_engine(url, pool_pre_ping=True)
        self.namespace = namespace
        self.table = Table("kalshi_market_ownership", MetaData(),
                           Column("namespace", String(100), primary_key=True),
                           Column("ticker", String(200), primary_key=True),
                           Column("owner", String(32), nullable=False))
        self.table.create(self.engine, checkfirst=True)

    def owner(self, ticker):
        with self.engine.connect() as conn:
            return conn.scalar(select(self.table.c.owner).where(
                self.table.c.namespace == self.namespace, self.table.c.ticker == ticker))

    def claim(self, ticker: str, owner: str):
        if not isinstance(ticker, str) or not ticker or len(ticker) > 200 or owner not in {"main", "chatgpt", "claude"}:
            raise ValueError("invalid market ownership identity")
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(self.table).values(namespace=self.namespace, ticker=ticker, owner=owner))
        except IntegrityError:
            if self.owner(ticker) != owner:
                raise ValueError("market owned by another book") from None

    def desk_markets(self):
        with self.engine.connect() as conn:
            return set(conn.scalars(select(self.table.c.ticker).where(
                self.table.c.namespace == self.namespace, self.table.c.owner != "main")))
