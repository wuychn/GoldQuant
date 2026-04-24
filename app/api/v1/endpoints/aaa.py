from akshare import stock_zh_index_spot_em, index_zh_a_hist

if __name__ == "__main__":
    index_zh_a_hist_df = index_zh_a_hist(
        symbol="932000",
        period="daily",
        start_date="20260423",
        end_date="20260424",
    )
    print(index_zh_a_hist_df)