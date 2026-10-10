"""Measured portfolio charts: no synthetic prices, flows or historical bot curves."""
import asyncio
import time

import pytest

from fakes import FakeFusion, _engine
from tradingbotty import account_history as history


@pytest.fixture
def account(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.settings.simulate = False
    return e


def sample(e, ts, total, flow=None, bots=None, currency='CHF', source='observed'):
    bots = bots or (None, None, None)
    e.db.execute('INSERT INTO account_samples VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (ts, currency, total, total/2, 0, flow, *bots, source))


def test_legacy_import_is_idempotent_and_never_invents_bot_history(account):
    e=account; now=time.time()
    e.db.set('wallet_hist',[[now-600,100,2],[now-300,102,None],[now,103,3]])
    e.db.set('account_history_v1',None)
    history.bootstrap(e); history.bootstrap(e)
    data=history.snapshot(e,'1d',now)
    assert data['samples']==3 and data['summary']['change']==3
    assert data['baseline'] is None and data['summary']['net_change'] is None
    assert all(b['bots']['brain'] is None and b['net'] is None for b in data['bars'])


def test_ohlc_preserves_actual_open_high_low_close_and_omits_empty_buckets(account):
    e=account; start=1800000000
    for dt,v in [(0,100),(60,110),(120,90),(180,105),(1800,120)]:sample(e,start+dt,v)
    d=history.snapshot(e,'1d',start+1801)
    assert d['bars'][0]['account']==[100,110,90,105]
    assert len(d['bars'])==2 and d['bars'][0]['n']==4
    assert d['summary']['high']==120 and d['summary']['low']==90
    assert d['summary']['change']==20


def test_deposit_is_not_a_profit_and_bot_results_have_common_baseline(account):
    e=account; start=1800000000
    sample(e,start,100,0,(2,3,0))
    sample(e,start+300,155,50,(4,4,-1))
    d=history.snapshot(e,'1d',start+301)
    assert d['summary']['change']==55
    assert d['summary']['net_change']==5
    assert d['summary']['flows']==50
    assert d['bars'][-1]['net']==[105,105,105,105]
    assert d['baseline']['bots']=={'brain':2,'fast':3,'volatility':0}


def test_record_reads_actual_book_pnl_including_open_acquisition_fees(account):
    e=account
    async def buy(*args):
        r=await FakeFusion.buy(e.live,*args); r['execution']['fee']=.15;return r
    e.live.buy=buy
    asyncio.run(e.execution.buy('SOL',30,'fake',book='fast'))
    asyncio.run(e._poll_wallet())
    d=history.snapshot(e)
    assert d['samples']==1 and d['bars'][0]['bots']['fast']==pytest.approx(-.15)
    assert d['bars'][0]['cash']==70
    asyncio.run(e._poll_wallet())
    assert history.snapshot(e)['samples']==1  # five-minute observation cadence


def test_missing_prices_and_changed_order_revision_are_not_recorded(account):
    e=account
    e.wallet={'currency':'CHF','total':100,'fiat':70}
    before=history.revision(e)
    assert not history.record(e,time.time(),{'FIAT':70,'SOL':3},{},before)
    assert 'Kurse fehlen' in e.db.get('account_chart_status')['error']
    e.db.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)',('changed',1,2,'fast','SOL','BUY','filled','{}','{}'))
    assert not history.record(e,time.time(),{'FIAT':100},{},before)
    assert not e.db.query('SELECT * FROM account_samples')


def test_flows_survive_more_than_fifty_events(account):
    e=account
    for _ in range(60):e.book_flow(5,'test')
    assert len(e.db.get('flows'))==50
    assert e.db.get('chart_flow_total')==300
    asyncio.run(e._poll_wallet())
    assert e.db.query('SELECT flow_total FROM account_samples')[0]['flow_total']==300


def test_long_history_remains_bounded_and_currency_separated(account):
    e=account;start=time.time()-365*86400
    e.db.executemany('INSERT INTO account_samples(ts,currency,total,source) VALUES(?,?,?,?)',
                     [(start+i*3600,'CHF',100+i%20,'legacy') for i in range(365*24)])
    sample(e,time.time()-100,999,currency='EUR')
    for period in history.RANGES:
        d=history.snapshot(e,period)
        assert len(d['bars'])<=402 and d['currency']=='CHF'
        assert d['summary']['high']<=119
    assert history.snapshot(e,'all')['samples']==365*24
    assert history.snapshot(e,'1d')['samples']<25


def test_empty_stale_and_unknown_range(account):
    e=account
    assert history.snapshot(e)['stale'] and history.snapshot(e)['bars']==[]
    sample(e,time.time()-800,100)
    assert history.snapshot(e)['stale']
    with pytest.raises(ValueError):history.snapshot(e,'typo')


def test_read_only_api_does_not_change_orders_or_activation(account,monkeypatch):
    from fastapi.testclient import TestClient
    from tradingbotty import server
    monkeypatch.setattr(server,'engine',account)
    client=TestClient(server.app)
    for period in ('1d','1w','1m','3m','6m','1y','all'):
        r=client.get('/api/account/history',params={'period':period})
        assert r.status_code==200 and r.json()['range']==period
    assert client.get('/api/account/history?period=typo').status_code==400
    assert not account.db.query('SELECT * FROM orders') and not account.volatility.cfg()['enabled']
    protected=TestClient(server.PasswordGate(server.app,'chart-secret'))
    assert protected.get('/api/account/history').status_code==401


def test_slow_or_currency_changed_read_is_not_a_fresh_measurement(account):
    e=account;e.wallet={'total':100,'fiat':100,'currency':'CHF'}
    assert not history.record(e,time.time()-121,{'FIAT':100},{},history.revision(e))
    assert 'zu alt' in e.db.get('account_chart_status')['error']
    e.wallet['currency']='EUR';e.settings['live']['currency']='EUR'
    assert not history.record(e,time.time(),{'FIAT':100},{},history.revision(e))
    assert 'Kostenbasis' in e.db.get('account_chart_status')['error']
