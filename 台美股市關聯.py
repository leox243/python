import yfinance as yf
import pandas as pd
from datetime import datetime

# 定義起始和結束日期
start_date = '1950-01-01'
end_date = datetime.now().strftime('%Y-%m-%d')

# 下載美國S&P 500指數數據
us_stock_data = yf.download('^GSPC', start=start_date, end=end_date, interval='1d')

# 下載台灣加權股價指數數據
taiwan_stock_data = yf.download('^TWII', start=start_date, end=end_date, interval='1d')

# 將兩個股市指數的收盤價合併到一個DataFrame中
stock_data = pd.DataFrame({
    'S&P 500': us_stock_data['Close'],
    'Taiwan Weighted': taiwan_stock_data['Close']
})

# 移除空值
stock_data.dropna(inplace=True)

# 計算兩個指數之間的相關係數
correlation = stock_data.corr()

# 輸出相關係數
print(correlation)