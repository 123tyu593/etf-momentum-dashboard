# -*- coding: utf-8 -*-
import json, os, sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

MOMENTUM_PERIOD = 20
MA_PERIOD = 30
MIN_HOLD_DAYS = 2
MOMENTUM_ADVANTAGE = 0.04
INITIAL_CAPITAL = 100000.0
TRANSACTION_COST = 0.001

ETFS = [
    {'code': '510300', 'name': '沪深300ETF'},
    {'code': '510500', 'name': '中证500ETF'},
    {'code': '159915', 'name': '创业板ETF'},
    {'code': '510180', 'name': '上证180ETF'},
    {'code': '512100', 'name': '中证1000ETF'},
    {'code': '513500', 'name': '标普500ETF'},
    {'code': '513100', 'name': '纳指ETF'},
    {'code': '159985', 'name': '豆粕ETF'},
    {'code': '518880', 'name': '黄金ETF'},
    {'code': '159934', 'name': '黄金ETF易方达'},
    {'code': '512690', 'name': '酒ETF'},
    {'code': '511260', 'name': '十年国债ETF'},
]

def get_data(code, days=150):
    import akshare as ak
    try:
        df = ak.fund_etf_hist_em(symbol=code, period='daily',
            start_date=(datetime.now() - timedelta(days=days+20)).strftime('%Y%m%d'),
            end_date=datetime.now().strftime('%Y%m%d'), adjust='qfq')
        if df is None or len(df) == 0: return None
        df = df.rename(columns={'日期': 'date', '收盘': 'close', '开盘': 'open', '最高': 'high', '最低': 'low', '成交量': 'volume'})
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df = df.sort_values('date').reset_index(drop=True)
        df['close'] = df['close'].astype(float)
        df['open'] = df['open'].astype(float)
        return df.tail(days)
    except Exception as e:
        print(f'  WARNING: {code} {e}')
        return None

def calc_indicators(df):
    if df is None or len(df) < MOMENTUM_PERIOD: return df
    df = df.copy()
    df['ma'] = df['close'].rolling(MA_PERIOD).mean()
    df['ret20'] = df['close'].pct_change(MOMENTUM_PERIOD)
    return df

def run():
    state_file = 'state.json'
    equity_file = 'equity.csv'
    trades_file = 'trades.csv'
    state = None
    equity = []
    trades = []
    if os.path.exists(state_file):
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
    if os.path.exists(equity_file):
        df = pd.read_csv(equity_file)
        equity = df.to_dict('records')
    if os.path.exists(trades_file):
        df = pd.read_csv(trades_file)
        trades = df.to_dict('records')
    if state is None:
        state = {'cash': INITIAL_CAPITAL, 'position': None, 'position_name': None,
            'shares': 0, 'hold_days': 0, 'cost_basis': 0, 'total_trades': 0,
            'start_date': None, 'last_update': None}
    print('[1/3] Fetching data...')
    all_data = {}
    for etf in ETFS:
        df = get_data(etf['code'])
        if df is not None and len(df) > MA_PERIOD + 10:
            all_data[etf['code']] = calc_indicators(df)
            print(f'  OK {etf["name"]} ({etf["code"]}): {len(df)} days')
    if not all_data:
        print('ERROR: No data fetched')
        return
    all_dates = sorted(set().union(*[set(df['date'].tolist()) for df in all_data.values()]))
    print(f'[2/3] Running strategy ({len(all_dates)} days)...')
    last_date = state.get('last_update')
    start_idx = 0
    if last_date:
        for i, d in enumerate(all_dates):
            if d > last_date:
                start_idx = i
                break
    for i in range(start_idx, len(all_dates)):
        today = all_dates[i]
        yesterday = all_dates[i-1] if i > 0 else None
        if not yesterday:
            continue
        rankings = []
        for code, df in all_data.items():
            row = df[df['date'] == yesterday]
            if len(row) > 0:
                r = row.iloc[0]
                if pd.notna(r.get('ret20')) and pd.notna(r.get('ma')):
                    name = next((e['name'] for e in ETFS if e['code'] == code), code)
                    rankings.append({'code': code, 'ret20': r['ret20'], 'ma': r['ma'], 'close': r['close'], 'name': name})
        if not rankings:
            continue
        rankings.sort(key=lambda x: x['ret20'], reverse=True)
        top = rankings[0]
        today_prices = {}
        for code, df in all_data.items():
            row = df[df['date'] == today]
            if len(row) > 0:
                today_prices[code] = row.iloc[0]
        action = 'hold'
        sell_reason = ''
        if state['position']:
            pos_data = None
            for r in rankings:
                if r['code'] == state['position']:
                    pos_data = r
                    break
            should_sell = False
            if pos_data:
                if pos_data['close'] < pos_data['ma']:
                    should_sell = True
                    sell_reason = '跌破均线'
                elif rankings.index(pos_data) > 0:
                    second = rankings[0]
                    if second['ret20'] - pos_data['ret20'] > MOMENTUM_ADVANTAGE:
                        should_sell = True
                        sell_reason = '排名下滑'
                if state['hold_days'] < MIN_HOLD_DAYS:
                    should_sell = False
                    sell_reason = ''
            if should_sell:
                sell_price = today_prices[state['position']]['open'] if state['position'] in today_prices else pos_data['close']
                sell_value = state['shares'] * sell_price
                fee = sell_value * TRANSACTION_COST
                state['cash'] = sell_value - fee
                ret = (sell_price / state['cost_basis'] - 1) * 100 if state['cost_basis'] > 0 else 0
                pnl = sell_value - state['shares'] * state['cost_basis'] - fee
                trades.append({'date': today, 'action': '卖出', 'code': state['position'],
                    'name': state['position_name'], 'price': round(sell_price, 3),
                    'shares': state['shares'], 'amount': round(sell_value, 2),
                    'fee': round(fee, 2), 'pnl': round(pnl, 2), 'pnl_pct': round(ret, 2),
                    'hold_days': state['hold_days'], 'reason': sell_reason})
                state['position'] = None
                state['position_name'] = None
                state['shares'] = 0
                state['hold_days'] = 0
                state['cost_basis'] = 0
                state['total_trades'] += 1
                action = 'sell'
        if not state['position']:
            if top['ret20'] > 0 and top['close'] > top['ma']:
                buy_price = today_prices[top['code']]['open'] if top['code'] in today_prices else top['close']
                fee = state['cash'] * TRANSACTION_COST
                available = state['cash'] - fee
                shares = int(available / buy_price / 100) * 100
                if shares > 0:
                    buy_value = shares * buy_price
                    state['cash'] = state['cash'] - buy_value - fee
                    state['position'] = top['code']
                    state['position_name'] = top['name']
                    state['shares'] = shares
                    state['hold_days'] = 0
                    state['cost_basis'] = buy_price
                    state['total_trades'] += 1
                    trades.append({'date': today, 'action': '买入', 'code': top['code'],
                        'name': top['name'], 'price': round(buy_price, 3),
                        'shares': shares, 'amount': round(buy_value, 2),
                        'fee': round(fee, 2), 'pnl': '', 'pnl_pct': '',
                        'hold_days': 0, 'reason': '动量第1名 + 站稳均线'})
                    action = 'buy'
        if state['position'] and action != 'buy':
            state['hold_days'] += 1
        pos_val = 0
        if state['position'] and state['position'] in today_prices:
            pos_val = state['shares'] * today_prices[state['position']]['close']
        total = state['cash'] + pos_val
        equity.append({'date': today, 'value': round(total, 2),
            'position': state['position_name'] if state['position'] else '空仓'})
        state['last_update'] = today
        if state['start_date'] is None:
            state['start_date'] = today
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    pd.DataFrame(equity).to_csv(equity_file, index=False, encoding='utf-8-sig')
    pd.DataFrame(trades).to_csv(trades_file, index=False, encoding='utf-8-sig')
    print('[3/3] Done')
    pos_val = 0
    if state['position']:
        for code, df in all_data.items():
            if code == state['position']:
                last = df.iloc[-1]
                pos_val = state['shares'] * last['close']
                break
    total = state['cash'] + pos_val
    print(f'  Total: ¥{total:.2f}')
    print(f'  Position: {state.get("position_name", "Empty")}')
    print(f'  Trades: {state.get("total_trades", 0)}')
    print(f'  Days: {len(equity)}')

if __name__ == '__main__':
    run()
