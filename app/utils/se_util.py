import akshare as ak

from app.utils.common_util import today
from app.utils.dataframe import dataframe_to_records


def all_stock():
    sse = dataframe_to_records(ak.stock_sse_summary())
    xsse = dataframe_to_records(ak.stock_szse_summary(date=today()))
    print(sse)
    print(xsse)


if __name__ == "__main__":
    all_stock()
    # print(json.dumps(jbxx(600519), ensure_ascii=False, indent=2))
