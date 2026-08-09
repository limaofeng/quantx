"""Local QMT resampling diagnostic; never imported by server processes."""

import numpy as np
import pandas as pd
from xtquant import xtdata

stock_list = ['688213.SH']
start_time = "20260120"
end_time = "20260121"

# xtdata.download_history_data2(
#     stock_list = stock_list,
#     period='tick',
#     start_time=start_time,
#     end_time=end_time,
#     callback=lambda x: print(f"Downloaded history data for {x}")
# )

# xtdata.download_history_data2(
#     stock_list = stock_list,
#     period='1m',
#     start_time=start_time,
#     end_time=end_time,
#     callback=lambda x: print(f"Downloaded history data for {x}")
# )
# xtdata.download_history_data2(
#     stock_list = stock_list,
#     period='5m',
#     start_time=start_time,
#     end_time=end_time,
#     callback=lambda x: print(f"Downloaded history data for {x}")
# )


market_data = xtdata.get_market_data_ex(
    stock_list=stock_list,
    start_time=start_time,
    end_time=end_time,
    period='1m',
    fill_data=True
)[stock_list[0]]

print(f"market_data size: {len(market_data)}")
print(market_data.columns)
print(f"原始数据:\n {market_data.head(17)[['time', 'open', 'high', 'low', 'close', 'preClose', 'volume', 'amount']]}")

def resample_to_5m(df: pd.DataFrame):
    """
    将1分钟数据重新采样为5分钟数据
    将09:30的数据与09:35的数据合并到第一个5分钟区间

    Parameters:
    df (pandas.DataFrame): 1分钟K线数据

    Returns:
    pandas.DataFrame: 5分钟K线数据
    """
    if df.empty:
        return df

    # 复制数据避免修改原数据
    df_copy = df.copy()

    # 一次性转换所有时间戳为中国时区datetime
    dt_series = pd.to_datetime(df_copy['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')

    # 保存所有09:30集合竞价数据
    auction_mask = (dt_series.dt.hour == 9) & (dt_series.dt.minute == 30)
    auction_df = df_copy[auction_mask].copy()

    # 删除所有09:30的那一行
    df_copy = df_copy[~auction_mask]

    # 直接用dt_series赋值，避免重复转换
    df_copy['datetime'] = dt_series[~auction_mask].values
    df_copy.set_index('datetime', inplace=True)

    # 定义聚合规则
    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'amount': 'sum',
        'preClose': 'first',
        'settelementPrice': 'last',
        'openInterest': 'last',
        'suspendFlag': 'last'
    }

    # 5分钟重采样
    resampled = df_copy.resample('5min', label='right', closed='right').agg(agg_dict)
    resampled.reset_index(inplace=True)
    # 统一用中国时区datetime
    resampled['time'] = resampled['datetime'].dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC').astype('int64') // 10**6
    resampled.drop('datetime', axis=1, inplace=True)
    column_order = df.columns.tolist()
    resampled = resampled[column_order]
    resampled = resampled.dropna(subset=['open', 'high', 'low', 'close'])

    # 批量合并auction_df到09:35
    if not auction_df.empty:
        # 先转换auction_df和resampled的日期
        auction_dt = pd.to_datetime(auction_df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
        resampled_dt = pd.to_datetime(resampled['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
        # 构造日期到09:35索引的映射
        resampled['__date'] = resampled_dt.dt.date
        resampled['__hour'] = resampled_dt.dt.hour
        resampled['__minute'] = resampled_dt.dt.minute
        idx_0935 = resampled[(resampled['__hour'] == 9) & (resampled['__minute'] == 35)].index
        date_to_idx = dict(zip(resampled.loc[idx_0935, '__date'], idx_0935))
        # 批量合并
        for i, row in auction_df.iterrows():
            day = auction_dt.loc[i].date()
            idx = date_to_idx.get(day, None)
            if idx is not None:
                merged = resampled.loc[idx].copy()
                for col in agg_dict:
                    rule = agg_dict[col]
                    if rule == 'first':
                        merged[col] = row[col]
                    elif rule == 'max':
                        merged[col] = max(row[col], merged[col])
                    elif rule == 'min':
                        merged[col] = min(row[col], merged[col])
                    elif rule == 'sum':
                        merged[col] = row[col] + merged[col]
                    elif rule == 'last':
                        # 通常 last 取 resampled 原值，这里可按需要调整
                        pass
                    else:
                        # 其他聚合方式，默认不处理
                        pass
                resampled.loc[idx] = merged
        resampled = resampled.drop(columns=['__date', '__hour', '__minute'])

    # 设置index为中国时区的5分钟字符串
    time_index = resampled['time'].apply(
        lambda x: pd.to_datetime(x, unit='ms', utc=True).tz_convert('Asia/Shanghai').replace(second=0).strftime('%Y%m%d%H%M%S')
    )
    resampled.index = time_index
    return resampled

# 先获取真实5分钟数据用于对比
real_5m_data = xtdata.get_market_data_ex(
    stock_list=stock_list,
    start_time=start_time,
    end_time=end_time,
    period='5m',
    fill_data=True
)[stock_list[0]]

print(f"real_5m_data size: {len(real_5m_data)}")
print(f"真实 5分钟数据 (前2行):\n {real_5m_data.tail(4)[['time', 'open', 'high', 'low', 'close', 'preClose', 'volume', 'amount']]}")

market_data_5m = resample_to_5m(market_data)
print(f"\nmarket_data_5m size: {len(market_data_5m)}")
print(market_data_5m.columns)
print(f"5分钟数据:\n {market_data_5m.tail(4)[['time', 'open', 'high', 'low', 'close', 'preClose', 'volume', 'amount']]}")

# 比较 market_data_5m 与 real_5m_data
def compare_5m_data(resampled_df, real_df, max_show: int = 50):
    """比较重采样数据与真实5分钟数据，并详细列出不匹配项。"""
    print("\n=== 数据一致性对比 ===")

    def fmt_ts(ms_val):
        try:
            return pd.to_datetime(ms_val, unit='ms', utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(ms_val)

    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']

    resampled_sorted = resampled_df.reset_index(drop=True).sort_values('time').reset_index(drop=True)
    real_sorted = real_df.reset_index(drop=True).sort_values('time').reset_index(drop=True)

    print(f"重采样数据行数: {len(resampled_sorted)}")
    print(f"真实数据行数: {len(real_sorted)}")

    if len(resampled_sorted) != len(real_sorted):
        print(f"行数不匹配: 重采样 {len(resampled_sorted)} vs 真实 {len(real_sorted)}")
        return

    mismatches = []
    for i in range(len(resampled_sorted)):
        resampled_row = resampled_sorted.iloc[i]
        real_row = real_sorted.iloc[i]

        row_time_match = resampled_row['time'] == real_row['time']
        row_detail = {
            'idx': i,
            'time_resampled': resampled_row['time'],
            'time_real': real_row['time'],
            'time_resampled_str': fmt_ts(resampled_row['time']),
            'time_real_str': fmt_ts(real_row['time']),
            'col_diffs': []
        }

        if not row_time_match:
            row_detail['col_diffs'].append('时间不匹配')

        for col in numeric_cols:
            res_val = resampled_row[col]
            real_val = real_row[col]
            if col in ['open', 'high', 'low', 'close']:
                equal = np.isclose(res_val, real_val, rtol=1e-6, atol=0)
            else:
                equal = res_val == real_val
            if not equal:
                row_detail['col_diffs'].append({
                    'col': col,
                    'resampled': res_val,
                    'real': real_val,
                    'diff': res_val - real_val if pd.notna(res_val) and pd.notna(real_val) else None
                })

        if row_detail['col_diffs']:
            mismatches.append(row_detail)

    if not mismatches:
        print("所有数据完全一致！")
        return

    print(f"发现 {len(mismatches)} 处不匹配，最多展示前 {max_show} 处：")
    for detail in mismatches[:max_show]:
        prefix = f"行 {detail['idx']}"
        time_part = f"时间 重采样 {detail['time_resampled']} ({detail['time_resampled_str']}) | 真实 {detail['time_real']} ({detail['time_real_str']})"
        print(f"{prefix}: {time_part}")
        for diff in detail['col_diffs']:
            if isinstance(diff, str):
                print(f"  - {diff}")
            else:
                print(f"  - 列 {diff['col']}: 重采样 {diff['resampled']} vs 真实 {diff['real']} (差值 {diff['diff']})")

# 执行比较
compare_5m_data(market_data_5m, real_5m_data)
