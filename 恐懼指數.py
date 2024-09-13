import requests
from bs4 import BeautifulSoup
import time
import datetime
import logging

# 設置日誌記錄
logging.basicConfig(filename='error.log', level=logging.ERROR, format='%(asctime)s %(message)s')

# 可增量的日期類
class IncrementableDate:
    def __init__(self, date_time=None, datestring=None, dateformat='%Y%m%d'):
        # 接受 datetime 或 日期字符串作為輸入
        if date_time is not None:
            self.value = date_time
        elif datestring is not None:
            self.value = datetime.datetime.strptime(datestring, dateformat)

    # 將日期轉換為字符串
    def to_string(self, dateformat='%Y%m%d'):
        return datetime.datetime.strftime(self.value, dateformat)

    # 增量增加日期，默認為一天
    def increment(self, offset=1):
        self.value += datetime.timedelta(days=offset)

    # 支持比較運算符：==, !=, >, <, >=, <=
    def __eq__(self, other):
        if isinstance(other, IncrementableDate):
            return self.value == other.value
        return NotImplemented

    def __ne__(self, other):
        return not self.__eq__(other)

    def __gt__(self, other):
        if isinstance(other, IncrementableDate):
            return self.value > other.value
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, IncrementableDate):
            return self.value < other.value
        return NotImplemented

    def __ge__(self, other):
        return self.__gt__(other) or self.__eq__(other)

    def __le__(self, other):
        return self.__lt__(other) or self.__eq__(other)


# 恐懼與貪婪指數抓取類
class FearAndGreed:
    BASE_URL = 'https://archive.org/wayback/available?url=money.cnn.com/data/fear-and-greed/&timestamp={TIMESTAMP}'

    def __init__(self, retries=5, wait_time=1):
        # 初始化重試次數和等待時間
        self.retries = retries
        self.wait_time = wait_time

    # 根據時間戳獲取歷史網頁快照的 URL
    def get_historical_url(self, timestamp='20190101'):
        for i in range(self.retries):
            try:
                # 發送請求到 Wayback Machine API
                resp = requests.get(self.BASE_URL.replace('{TIMESTAMP}', timestamp)).json()
                hist_url = resp['archived_snapshots']['closest']['url']
                return hist_url  # 如果獲取成功，直接返回 URL
            except KeyError:
                logging.error(f"KeyError at timestamp: {timestamp}, retrying {i+1}/{self.retries}")
                time.sleep(self.wait_time)  # 發生 KeyError，等待並重試
            except Exception as e:
                logging.error(f"Error: {e}, retrying {i+1}/{self.retries}")
                time.sleep(self.wait_time)  # 其他錯誤，記錄日誌並重試
        
        logging.error(f"Failed to get historical URL after {self.retries} retries for timestamp: {timestamp}")
        return None  # 重試後依然失敗，返回 None

    # 根據歷史 URL 獲取恐懼與貪婪指數
    def get_historical_score(self, historical_url=None, timestamp=None):
        if historical_url is None and timestamp is not None:
            historical_url = self.get_historical_url(timestamp)

        if historical_url is None:
            logging.error(f"Failed to get historical URL for timestamp: {timestamp}")
            return None

        try:
            # 發送請求並解析網頁內容
            resp = requests.get(historical_url).content
            soup = BeautifulSoup(resp, 'html.parser')
            chart = soup.find('div', {'id': 'needleChart'})
            current_score = chart.find('li').text
            current_score = current_score.replace('Fear & Greed Now: ', '').split(' ')[0]
            return current_score  # 返回當前的恐懼與貪婪指數
        except Exception as e:
            logging.error(f"Error while)