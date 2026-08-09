"""Local QMT diagnostic; never imported by server processes."""

import json

import pandas as pd
from xtquant import xtdata

xtdata.enable_hello = False
stock = '600519.SH'

print(f"Fetching ALL financial data for {stock}...")
# table_list=[] means fetch all available tables
data = xtdata.get_financial_data([stock], table_list=[], start_time='20200101')

res = data.get(stock, {})
output = {}

print(f"\nAvailable tables for {stock}: {list(res.keys())}")

for table_name, df in res.items():
    if isinstance(df, pd.DataFrame) and not df.empty:
        # Get columns and a sample of the first row
        info = {
            "columns": df.columns.tolist(),
            "sample": df.iloc[-1].to_dict()
        }
        # Convert non-serializable types for JSON
        for k, v in info["sample"].items():
            if pd.isna(v):
                info["sample"][k] = None
            elif hasattr(v, "isoformat"):
                info["sample"][k] = v.isoformat()
            elif hasattr(v, "item"):
                info["sample"][k] = v.item()  # for numpy types

        output[table_name] = info
    else:
        output[table_name] = "Empty or Not a DataFrame"

print("\n--- DATA SAMPLES ---")
print(json.dumps(output, indent=4, ensure_ascii=False))
