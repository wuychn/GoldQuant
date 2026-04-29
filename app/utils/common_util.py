import datetime
import threading
import time
from datetime import datetime, timedelta, date
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


def get_val(item: Any, field_name: str, default_value: Any = '') -> Any:
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

# 进程内按年缓存：年份 -> 该年全部法定「休」日期（成功拉取聚合接口后写入；失败不写入以便重试）
_YEAR_HOLIDAY_REST_CACHE: dict[int, frozenset[date]] = {}
_YEAR_HOLIDAY_CACHE_LOCK = threading.Lock()


def _fetch_year_holiday_rest_from_api(year: int) -> Optional[frozenset[date]]:
    """仅 HTTP 拉取某年休日集合，不读不写内存缓存。"""
    url = f"https://holiday.ailcc.com/api/holiday/year/{year}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                if attempt == MAX_RETRIES:
                    return None
                time.sleep(RETRY_DELAY)
                continue
            out: set[date] = set()
            for mmdd, info in (data.get("holiday") or {}).items():
                if not info.get("holiday"):
                    continue
                mo, da = mmdd.split("-", 1)
                out.add(date(year, int(mo), int(da)))
            return frozenset(out)
        except Exception:
            if attempt == MAX_RETRIES:
                return None
            time.sleep(RETRY_DELAY)
    return None


def _get_year_holiday_rest_days(year: int) -> Optional[frozenset[date]]:
    """
    获取某年法定「休」日期集合：先读内存逐年缓存；无该年时再请求聚合接口，
    成功则写入缓存，失败不写缓存便于下次调用重试。
    """
    with _YEAR_HOLIDAY_CACHE_LOCK:
        if year in _YEAR_HOLIDAY_REST_CACHE:
            return _YEAR_HOLIDAY_REST_CACHE[year]
        rest = _fetch_year_holiday_rest_from_api(year)
        if rest is not None:
            _YEAR_HOLIDAY_REST_CACHE[year] = rest
        return rest


@lru_cache(maxsize=4096)
def _is_real_workday_single_day_api(d: date) -> bool:
    """兜底：单日 info 接口，仅 type==0 计为工作日（排除补班周末 type=4 等）。"""
    url = f"https://holiday.ailcc.com/api/holiday/info/{d}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                return False
            wtype = data.get("type", {}).get("type")
            return wtype == 0
        except Exception:
            if attempt == MAX_RETRIES:
                return False
            time.sleep(RETRY_DELAY)
    return False


def _is_real_workday_cn(d: date) -> bool:
    """
    周一至周五：在法定「休」集合中则非工作日；周六日恒为否（不调接口）。
    年份接口失败时对该日回退单日查询。
    """
    if d.weekday() >= 5:
        return False
    rest = _get_year_holiday_rest_days(d.year)
    if rest is not None:
        return d not in rest
    return _is_real_workday_single_day_api(d)


def is_real_workday_cn(d: Optional[date] = None) -> bool:
    """
    是否为大陆「真实工作日」：周一至周五且非法定休日；周六日恒为否。
    口径与 holiday 单日接口 type==0 一致，可用于盘前/盘中/盘后接口兜底。
    """
    if d is None:
        d = datetime.now().date()
    return _is_real_workday_cn(d)


def get_n_workdays_ago(date_input: Optional[str] = None, n: int = 5) -> Optional[str]:
    """
    求基准日之前（不含基准日）第 n 个真实工作日：
    - 排除周末；
    - 排除法定节假日；
    - 排除调休补班日（与旧版单日接口 type=0 口径一致；周末补班不参与统计）。

    节假日数据：按年在进程内存中缓存聚合结果，某年尚无缓存时再请求接口；年份接口失败时再对该日单日查询兜底。

    至多先看近 n 个自然日组成的窗口 [基准日前第 n 天, 基准日前第 1 天]：在连续全是工作日时，
    第 n 个工作日必落在该窗口内（必不晚于「基准日前第 n 个自然日」）。若节假日较多导致窗口内工作日不足，
    再从「基准日前第 n+1 天」往更早回溯。

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

    if n <= 0:
        return None

    # 预取可能涉及年份，正常情况整次调用仅 1～2 次年历请求
    earliest = base_date - timedelta(days=max(n, 365))
    for y in range(earliest.year, base_date.year + 1):
        _get_year_holiday_rest_days(y)

    max_search_days = 365  # 最多回溯一年，防止无限循环
    workday_count = 0

    # 阶段一：只在 [base_date - n, base_date - 1]（至多 n 个自然日）内找；
    # 至多 n 个连续自然日中最多出现 n 个工作日，若第 n 个工作日存在且中间无长假空档，必落在此区间内。
    phase1_end = base_date - timedelta(days=n)
    cur_date = base_date - timedelta(days=1)

    for _ in range(max_search_days):
        if cur_date < phase1_end:
            break
        if cur_date.weekday() >= 5:
            cur_date -= timedelta(days=1)
            continue
        if _is_real_workday_cn(cur_date):
            workday_count += 1
            if workday_count == n:
                return cur_date.strftime('%Y%m%d')
        cur_date -= timedelta(days=1)

    # 阶段二：区间内工作日不足（含节假日多时），从区间外再往更早回溯
    for _ in range(max_search_days):
        if cur_date.weekday() >= 5:
            cur_date -= timedelta(days=1)
            continue
        if _is_real_workday_cn(cur_date):
            workday_count += 1
            if workday_count == n:
                return cur_date.strftime('%Y%m%d')
        cur_date -= timedelta(days=1)

    return None


if __name__ == "__main__":
    # 测试用例（具体日期依赖节假日接口）
    print(get_n_workdays_ago("2026-10-08", n=5))
    print(get_n_workdays_ago("2026-10-08", n=6))
    print(get_n_workdays_ago("2026-10-08", n=7))
    print(get_n_workdays_ago("2026-10-08", n=8))
    print(get_n_workdays_ago("2026-10-08", n=9))

    # 今天
    result = get_n_workdays_ago()
    print(result)
