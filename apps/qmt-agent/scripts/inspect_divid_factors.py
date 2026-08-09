"""Local QMT dividend-factor diagnostic; never imported by server processes."""


import pandas as pd
from xtquant import xtdata

xtdata.enable_hello = False
stock = '600519.SH'

data = xtdata.get_divid_factors(stock, "20230501", "20260131")
df = pd.DataFrame(data)
print(df)
