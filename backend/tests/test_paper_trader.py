"""Tests for the paper trading service using in-memory SQLite."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from src.db.models import Base, PaperTrade, Balance, TradeStatus
from src.db.session import get_session_factory
from src.engine.arbitrage import ArbitrageOpportunity
from src.engine.paper_trader import PaperTrader
from src.models.ticker import Exchange


def make_opportunity(
    symbol="BTC-USD",
    buy_ex=Exchange.BINANCE,
    sell_ex=Exchange.KRAKEN,
    buy_price=100_000.0,
    sell_price=101_000.0,
    buy_qty=1.0,
    sell_qty=1.0,
    gross_pct=1.0,
    fees_pct=0.36,
    slippage_pct=0.10,
    net_pct=0.54,
):
    return ArbitrageOpportunity(
        symbol=symbol,
        buy_exchange=buy_ex,
        sell_exchange=sell_ex,
        buy_price=buy_price,
        sell_price=sell_price,
        buy_qty=buy_qty,
        sell_qty=sell_qty,
        gross_profit_pct=gross_pct,
        total_fees_pct=fees_pct,
        slippage_pct=slippage_pct,
        net_profit_pct=net_pct,
    )


@pytest.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def trader(db_engine):
    # Patch the session factory to use our test engine
    import src.db.session as sess_mod
    sess_mod._engine = db_engine
    sess_mod._session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    t = PaperTrader(engine=db_engine, initial_balance=10_000.0, cooldown_seconds=0)
    await t.initialize()
    return t


@pytest.mark.asyncio
async def test_initial_balance_recorded(trader, db_engine):
    async with AsyncSession(db_engine) as session:
        result = await session.execute(select(Balance))
        balances = result.scalars().all()
    assert len(balances) == 1
    assert balances[0].balance == 10_000.0
    assert balances[0].reason == "initial"


@pytest.mark.asyncio
async def test_execute_trade(trader, db_engine):
    opp = make_opportunity()
    result = await trader.execute(opp)

    assert not result.skipped
    assert result.balance_before == 10_000.0
    assert result.balance_after > result.balance_before  # profitable trade
    assert trader.total_trades == 1
    assert trader.total_profit > 0

    # Check database
    async with AsyncSession(db_engine) as session:
        trades = (await session.execute(select(PaperTrade))).scalars().all()
    assert len(trades) == 1
    assert trades[0].symbol == "BTC-USD"
    assert trades[0].buy_exchange == "binance"
    assert trades[0].sell_exchange == "kraken"
    assert trades[0].net_profit > 0
    assert trades[0].status == TradeStatus.EXECUTED


@pytest.mark.asyncio
async def test_position_sizing_respects_max(trader):
    # With $10,000 balance and 10% max, should use at most $1,000
    opp = make_opportunity(buy_price=50_000.0, sell_price=51_000.0)
    result = await trader.execute(opp)

    assert not result.skipped
    # Max position = 10% of 10,000 = $1,000, qty = 1000/50000 = 0.02
    assert result.trade.quantity <= 0.021  # small epsilon


@pytest.mark.asyncio
async def test_cooldown_prevents_rapid_trades(db_engine):
    import src.db.session as sess_mod
    sess_mod._engine = db_engine
    sess_mod._session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    trader = PaperTrader(
        engine=db_engine, initial_balance=10_000.0, cooldown_seconds=60
    )
    await trader.initialize()

    opp = make_opportunity()
    r1 = await trader.execute(opp)
    assert not r1.skipped

    r2 = await trader.execute(opp)
    assert r2.skipped
    assert "Cooldown" in r2.skip_reason


@pytest.mark.asyncio
async def test_skip_negative_profit(trader):
    opp = make_opportunity(net_pct=-0.5)
    result = await trader.execute(opp)
    assert result.skipped
    assert "profit" in result.skip_reason.lower()


@pytest.mark.asyncio
async def test_balance_updates_after_multiple_trades(trader, db_engine):
    opp1 = make_opportunity(symbol="BTC-USD")
    opp2 = make_opportunity(symbol="ETH-USD", buy_price=2000.0, sell_price=2020.0)

    r1 = await trader.execute(opp1)
    r2 = await trader.execute(opp2)

    assert not r1.skipped
    assert not r2.skipped
    assert trader.total_trades == 2
    assert r2.balance_before == r1.balance_after

    # Check balance snapshots in DB
    async with AsyncSession(db_engine) as session:
        balances = (await session.execute(
            select(Balance).order_by(Balance.id)
        )).scalars().all()
    # initial + 2 trades = 3 balance records
    assert len(balances) == 3
    assert balances[0].reason == "initial"
    assert balances[1].reason == "trade"
    assert balances[2].reason == "trade"
