"""Local QMT instrument diagnostic; never imported by server processes."""

from xtquant import xtdata

details = xtdata.get_instrument_detail_list(stock_list=["002759.SZ","603395.SH"], iscomplete=True)

for key in details:
    print(key, details[key])
