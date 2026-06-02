import json

if __name__ == "__main__":
    path = 'D:\workspace\GoldQuant\data\\b.json'
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for i in raw['data']['盘口异动']:
        if i['股票代码'].startswith('688'):
            print(i)