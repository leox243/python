import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from matplotlib.widgets import CheckButtons
import mplcursors  # 用於互動式游標

# 設定中文字體
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei']
plt.rcParams['axes.unicode_minus'] = False

# 定義時間範圍
start_date = datetime(1950, 1, 1)
end_date = datetime.now()  # 取得今天日期

# 下載美國S&P 500指數的數據
us_stock_data = yf.download('^GSPC', start=start_date, end=end_date, interval='1wk')

# 下載台灣加權股價指數數據
try:
    taiwan_stock_data = yf.download('^TWII', start=start_date, end=end_date, interval='1wk')
except Exception as e:
    print(f"yfinance 無法取得台灣加權股價指數的數據: {e}")
    taiwan_stock_data = None

# 設定美國與台灣的總統大選年份
us_election_years = [1952, 1956, 1960, 1964, 1968, 1972, 1976, 1980, 1984, 1988, 1992, 1996, 2000, 2004, 2008, 2012, 2016, 2020]
taiwan_election_years = [1996, 2000, 2004, 2008, 2012, 2016, 2020]

# 創建圖表
fig, ax = plt.subplots(figsize=(14, 8))

# 繪製美國S&P 500指數
us_plot, = ax.plot(us_stock_data.index, us_stock_data['Close'], label='美國股市指數 (S&P 500)')

# 繪製台灣加權股價指數（如果存在）
if taiwan_stock_data is not None:
    taiwan_plot, = ax.plot(taiwan_stock_data.index, taiwan_stock_data['Close'], label='台灣股市指數 (加權股價指數)', color='orange')

# 標記美國總統大選日期
for year in us_election_years:
    election_day = datetime(year, 11, 1) + pd.DateOffset(weekday=1)  # 每年11月的第一個星期二
    ax.axvline(election_day, color='red', linestyle='--', alpha=0.5, label=f'美國大選 {year}' if year == us_election_years[0] else "")

# 標記台灣總統大選年份
for year in taiwan_election_years:
    election_day = datetime(year, 3, 1) + pd.DateOffset(weekday=6)  # 台灣總統選舉一般在3月的第二個星期六
    ax.axvline(election_day, color='green', linestyle='--', alpha=0.5, label=f'台灣大選 {year}' if year == taiwan_election_years[0] else "")

# **新增完整重大事件標註**
events = {
    "韓戰 (1950-1953)": datetime(1950, 6, 25),
    "亞洲流感 (1957)": datetime(1957, 2, 1),
    "古巴導彈危機 (1962)": datetime(1962, 10, 16),
    "越南戰爭 (1965-1975)": datetime(1965, 11, 1),
    "甘迺迪遇刺 (1963)": datetime(1963, 11, 22),
    "第四次中東戰爭 (1973)": datetime(1973, 10, 6),
    "唐山大地震 (1976)": datetime(1976, 7, 28),
    "伊朗伊斯蘭革命 (1979)": datetime(1979, 2, 11),
    "聖海倫火山爆發 (1980)": datetime(1980, 5, 18),
    "切爾諾貝利核災 (1986)": datetime(1986, 4, 26),
    "柏林圍牆倒塌 (1989)": datetime(1989, 11, 9),
    "波斯灣戰爭 (1991)": datetime(1991, 1, 17),
    "盧旺達種族大屠殺 (1994)": datetime(1994, 4, 7),
    "亞洲金融危機 (1997)": datetime(1997, 7, 2),
    "911恐怖襲擊 (2001)": datetime(2001, 9, 11),
    "印度洋海嘯 (2004)": datetime(2004, 12, 26),
    "全球金融危機 (2008)": datetime(2008, 9, 15),
    "海地地震 (2010)": datetime(2010, 1, 12),
    "阿拉伯之春 (2011)": datetime(2011, 12, 17),
    "西非伊波拉疫情 (2014)": datetime(2014, 3, 23),
    "COVID-19大流行 (2020)": datetime(2020, 3, 11),
    "俄烏戰爭 (2022)": datetime(2022, 2, 24),
    "摩洛哥地震 (2023)": datetime(2023, 9, 8)
}

# 使用 plt.text() 將事件文字顯示為直列方式（縱列）
for event, date in events.items():
    vertical_text = '\n'.join(event)  # 將事件名稱變為縱列
    ax.text(date, us_stock_data['Close'].max() * 1.05, vertical_text, fontsize=9, color='blue', rotation=0, verticalalignment='top')

# 設定X軸格式
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
ax.xaxis.set_major_locator(mdates.YearLocator(5))

# 追蹤繪製的水平和垂直線，以便在更新時刪除它們
vline = None
hline = None

# 新增互動游標功能，當鼠標懸停在曲線上顯示數值
cursor = mplcursors.cursor(hover=True)

# 在游標上顯示垂直和水平線，並清除舊的線條
@cursor.connect("add")
def on_add(sel):
    global vline, hline
    # 刪除舊的線條
    if vline:
        vline.remove()
    if hline:
        hline.remove()
    
    sel.annotation.set_text(
        f"日期: {mdates.num2date(sel.target[0]).strftime('%Y-%m-%d')}\n指數: {sel.target[1]:.2f}")
    # 添加垂直和水平線
    sel.annotation.get_bbox_patch().set(fc="white", alpha=0.8)
    vline = sel.artist.axes.axvline(sel.target[0], color='gray', linestyle='--', alpha=0.7)
    hline = sel.artist.axes.axhline(sel.target[1], color='gray', linestyle='--', alpha=0.7)

# 添加曲線開關按鈕
rax = plt.axes([0.02, 0.4, 0.1, 0.15])  # 添加一個小區域來放置開關按鈕
check = CheckButtons(rax, ['美國股市指數', '台灣股市指數'], [True, True])

# 定義開關按鈕的功能
def func(label):
    if label == '美國股市指數':
        us_plot.set_visible(not us_plot.get_visible())  # 切換美國股市指數的可見性
    elif label == '台灣股市指數':
        taiwan_plot.set_visible(not taiwan_plot.get_visible())  # 切換台灣股市指數的可見性
    plt.draw()

# 連接開關按鈕與功能
check.on_clicked(func)

plt.tight_layout()
plt.show()
