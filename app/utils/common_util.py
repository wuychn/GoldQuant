import datetime
import time
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, List
from typing import Optional

import requests


def list_to_dict(data_list: list[dict]) -> dict:
    """
    工具方法：
    列表中每个 dict 取【第一个值】作为 key
                【第二个值】作为 value
    最终合并成一个字典

    示例：
    输入：[{"item":"统计日期","value":"20240101"}]
    输出：{"统计日期":"20240101"}
    """
    result = {}

    if not isinstance(data_list, list):
        return result

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # 取出所有值 -> 第一个是 key，第二个是 value
        values = list(item.values())
        if len(values) >= 2:
            key = values[0]
            value = values[1]
            result[key] = value

    return result


def format_percent(num):
    """
     四舍五入保留2位小数，并在最后追加%
     """
    if num:
        res = round(num, 2)
        return f"{res}%"
    return ''


def today():
    """
    获取当日日期
    """
    return datetime.now().strftime("%Y%m%d")


def today():
    """
    获取当日日期
    """
    return datetime.now().strftime("%Y%m%d")


def today_before(days):
    """
    自然天，非工作日
    :param days:
    :return:
    """
    from datetime import datetime, timedelta
    day5 = datetime.now() - timedelta(days=days)
    return day5.strftime("%Y%m%d")


def sort_by_field_desc_and_limit(
        data_list: List[Any],
        field_name: str,
        limit: int = 10,
        desc: bool = True
) -> List[Any]:
    """
        自动判断元素是 对象 还是 字典，统一按指定字段降序排序，并返回前 N 条

        Args:
            data_list: 列表（元素可以是对象，也可以是 dict）
            field_name: 排序字段名（对象属性名 / 字典 key）
            limit: 取前多少条，默认 10
            desc: 是否降序，默认 True

        Returns:
            排序 + 截取后的新列表，不修改原列表
        """
    # 空值直接返回空
    if not data_list or not field_name:
        return []

    # 核心：自动判断是对象还是字典
    def get_sort_key(item: Any) -> Any:
        if isinstance(item, dict):
            # 如果是字典 → 用 key 获取
            return item.get(field_name)
        else:
            # 如果是普通对象 → 用属性获取
            return getattr(item, field_name, None)

    # 降序排序
    sorted_list = sorted(
        data_list,
        key=get_sort_key,
        reverse=desc
    )

    # 返回前 limit 条
    return sorted_list[:limit]


def filter_exclude_by_key(
        data_list: List[Any],
        field_name: str,
        exclude_values: List[Any]
) -> List[Any]:
    """
    过滤掉列表中【指定key/字段 = 指定值】的所有元素

    Args:
        data_list: 原始列表（元素可以是 dict 或 对象）
        field_name: 要判断的 key / 属性名
        exclude_values: 要排除的值列表（只要等于其中任意一个，就被过滤掉）

    Returns:
        过滤后的新列表（不会修改原列表）
    """
    if not data_list or not field_name or exclude_values is None:
        return data_list

    result = []
    for item in data_list:
        # 获取当前元素的字段值（自动判断 dict / 对象）
        if isinstance(item, dict):
            value = item.get(field_name)
        else:
            value = getattr(item, field_name, None)

        # 如果值不在排除列表里 → 保留
        if value not in exclude_values:
            result.append(item)

    return result


def format_sci_to_decimal(num, decimal=2) -> float:
    """
    科学计数法数值 转 普通小数，四舍五入保留指定位数
    :param num: 可以是 int / float / 科学计数字符串 如 1.23e-5、"9.876e+3"
    :param decimal: 保留小数位数，默认2位
    :return: 四舍五入后的普通浮点数
    """
    # 先统一转为浮点数，自动识别科学计数法
    float_val = float(num)
    # 四舍五入保留两位小数
    res = round(float_val, decimal)
    return res


def get_val(item: Any, field_name: str, default_value: Any) -> Any:
    # 自动判断 dict / 对象
    if isinstance(item, dict):
        return item.get(field_name, default_value)
    else:
        return getattr(item, field_name, default_value)


def set_field_value(item: Any, field_name: str, value: Any) -> Any:
    """
    通用修改字段值：自动判断是 dict 还是 对象
    :param item: 字典 或 普通对象
    :param field_name: 要修改的 key / 属性名
    :param value: 新值
    :return: 修改后的 item（原地修改，也会返回）
    """
    if isinstance(item, dict):
        # 字典：直接赋值 key
        item[field_name] = value
    else:
        # 对象：直接赋值属性
        setattr(item, field_name, value)

    return item


def cal_avg(data, f):
    try:
        if data:
            s = 0.0
            for h in data:
                s = s + get_val(h, f, 0)
            return round(s / len(data), 2)
    except Exception as e:
        print(e)
    return None


# 可选：配置重试次数
MAX_RETRIES = 2
RETRY_DELAY = 0.5  # 秒


def get_n_workdays_ago(date_input: Optional[str] = None, n: int = 5) -> Optional[str]:
    """
    从基准日期前一天开始，向前找第 n 个真实工作日（排除周末、法定节假日、补班周末）
    返回格式: yyyyMMdd，找不到返回 None
    """
    # 解析基准日期
    if date_input is None:
        base_date = datetime.now().date()
    else:
        try:
            base_date = datetime.strptime(date_input, '%Y-%m-%d').date()
        except ValueError:
            return None

    @lru_cache(maxsize=2048)
    def is_real_workday(d: datetime.date) -> bool:
        """调用 API 判断是否为真实工作日，带简单重试"""
        url = f"https://holiday.ailcc.com/api/holiday/info/{d}"
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0:
                    return False
                wtype = data.get("type", {}).get("type")
                # 只有 type=0 才是我们需要的工作日（排除补班周末 type=4）
                return wtype == 0
            except Exception as e:
                if attempt == MAX_RETRIES:
                    # 重试失败后，保守起见视为非工作日（避免程序崩溃）
                    return False
                time.sleep(RETRY_DELAY)
        return False

    # 从基准日期的前一天开始
    cur_date = base_date - timedelta(days=1)
    workday_count = 0
    max_search_days = 365  # 最多回溯一年，防止无限循环

    for _ in range(max_search_days):
        # 步骤1: 本地快速判断周末（5=周六, 6=周日）
        if cur_date.weekday() >= 5:
            cur_date -= timedelta(days=1)
            continue

        # 步骤2: 非周末，调用 API 判断是否为真实工作日
        if is_real_workday(cur_date):
            workday_count += 1
            if workday_count == n:
                return cur_date.strftime('%Y%m%d')

        # 继续向前移动一天
        cur_date -= timedelta(days=1)

    return None


if __name__ == "__main__":
    # 测试用例
    # print(get_n_workdays_ago("2026-10-08", n=5))  # 期望 20260923
    # print(get_n_workdays_ago("2026-10-08", n=6))
    # print(get_n_workdays_ago("2026-10-08", n=7))
    # print(get_n_workdays_ago("2026-10-08", n=8))
    # print(get_n_workdays_ago("2026-10-08", n=9))

    # 今天
    result = get_n_workdays_ago()
    print(result)
