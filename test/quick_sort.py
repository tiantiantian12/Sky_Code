"""
快速排序算法实现
包含：标准快速排序、随机化快速排序、三向切分快速排序
"""

import random
import time
from typing import List, Callable, Any


def quick_sort(arr: List[int]) -> List[int]:
    """
    标准快速排序（Lomuto分区方案）
    时间复杂度：平均 O(n log n)，最坏 O(n²)
    空间复杂度：O(log n)
    """
    if len(arr) <= 1:
        return arr.copy()
    
    result = arr.copy()
    _quick_sort_inplace(result, 0, len(result) - 1)
    return result


def _quick_sort_inplace(arr: List[int], low: int, high: int) -> None:
    """原地快速排序"""
    if low < high:
        pivot_index = _partition(arr, low, high)
        _quick_sort_inplace(arr, low, pivot_index - 1)
        _quick_sort_inplace(arr, pivot_index + 1, high)


def _partition(arr: List[int], low: int, high: int) -> int:
    """Lomuto分区方案"""
    pivot = arr[high]  # 选择最后一个元素作为基准
    i = low - 1
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quick_sort_randomized(arr: List[int]) -> List[int]:
    """
    随机化快速排序
    通过随机选择基准元素，避免最坏情况
    """
    if len(arr) <= 1:
        return arr.copy()
    
    result = arr.copy()
    _quick_sort_randomized_inplace(result, 0, len(result) - 1)
    return result


def _quick_sort_randomized_inplace(arr: List[int], low: int, high: int) -> None:
    """随机化原地快速排序"""
    if low < high:
        pivot_index = _partition_random(arr, low, high)
        _quick_sort_randomized_inplace(arr, low, pivot_index - 1)
        _quick_sort_randomized_inplace(arr, pivot_index + 1, high)


def _partition_random(arr: List[int], low: int, high: int) -> int:
    """随机分区"""
    rand_index = random.randint(low, high)
    arr[rand_index], arr[high] = arr[high], arr[rand_index]
    return _partition(arr, low, high)


def quick_sort_3way(arr: List[int]) -> List[int]:
    """
    三向切分快速排序（Dutch National Flag）
    适用于大量重复元素的数组
    """
    if len(arr) <= 1:
        return arr.copy()
    
    result = arr.copy()
    _quick_sort_3way_inplace(result, 0, len(result) - 1)
    return result


def _quick_sort_3way_inplace(arr: List[int], low: int, high: int) -> None:
    """三向切分原地快速排序"""
    if low >= high:
        return
    
    # 三向切分
    pivot = arr[low]
    lt = low      # arr[low..lt-1] < pivot
    gt = high     # arr[gt+1..high] > pivot
    i = low + 1   # arr[lt..i-1] == pivot
    
    while i <= gt:
        if arr[i] < pivot:
            arr[lt], arr[i] = arr[i], arr[lt]
            lt += 1
            i += 1
        elif arr[i] > pivot:
            arr[i], arr[gt] = arr[gt], arr[i]
            gt -= 1
        else:
            i += 1
    
    # 递归排序小于和大于pivot的部分
    _quick_sort_3way_inplace(arr, low, lt - 1)
    _quick_sort_3way_inplace(arr, gt + 1, high)


def quick_sort_custom(arr: List[Any], key: Callable[[Any], Any] = None, reverse: bool = False) -> List[int]:
    """
    自定义比较函数的快速排序
    """
    if len(arr) <= 1:
        return arr.copy()
    
    result = arr.copy()
    _quick_sort_custom_inplace(result, 0, len(result) - 1, key, reverse)
    return result


def _quick_sort_custom_inplace(arr: List[Any], low: int, high: int, key: Callable, reverse: bool) -> None:
    """自定义比较的原地快速排序"""
    if low < high:
        pivot_index = _partition_custom(arr, low, high, key, reverse)
        _quick_sort_custom_inplace(arr, low, pivot_index - 1, key, reverse)
        _quick_sort_custom_inplace(arr, pivot_index + 1, high, key, reverse)


def _partition_custom(arr: List[Any], low: int, high: int, key: Callable, reverse: bool) -> int:
    """自定义分区"""
    pivot = arr[high]
    pivot_key = key(pivot) if key else pivot
    i = low - 1
    
    for j in range(low, high):
        current_key = key(arr[j]) if key else arr[j]
        
        if reverse:
            should_swap = current_key >= pivot_key
        else:
            should_swap = current_key <= pivot_key
        
        if should_swap:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def benchmark(sort_func: Callable, arr: List[int], name: str) -> tuple:
    """基准测试函数"""
    start = time.perf_counter()
    result = sort_func(arr)
    end = time.perf_counter()
    elapsed = (end - start) * 1000  # 毫秒
    
    # 验证排序正确性
    is_correct = all(result[i] <= result[i+1] for i in range(len(result)-1))
    
    print(f"{name:25} | {elapsed:8.2f} ms | {'✓' if is_correct else '✗'}")
    return result, elapsed


if __name__ == "__main__":
    print("=" * 60)
    print("  快速排序算法演示")
    print("=" * 60)
    
    # 测试数据
    test_cases = [
        [64, 34, 25, 12, 22, 11, 90],
        [3, -1, 0, 5, -2],
        [5, 5, 5, 5, 5],
        [1],
        [],
        list(range(10, 0, -1)),
    ]
    
    print("\n📋 基本功能测试:")
    print("-" * 60)
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n测试 {i}: {test}")
        sorted_arr = quick_sort(test)
        print(f"  标准快排: {sorted_arr}")
        
        sorted_arr_rand = quick_sort_randomized(test)
        print(f"  随机快排: {sorted_arr_rand}")
        
        sorted_arr_3way = quick_sort_3way(test)
        print(f"  三向快排: {sorted_arr_3way}")
    
    # 自定义排序测试
    print("\n\n📋 自定义排序测试:")
    print("-" * 60)
    
    students = [
        {"name": "Alice", "score": 85},
        {"name": "Bob", "score": 92},
        {"name": "Charlie", "score": 78},
        {"name": "David", "score": 95},
    ]
    
    print("\n按成绩排序:")
    sorted_students = quick_sort_custom(students, key=lambda x: x["score"])
    for s in sorted_students:
        print(f"  {s['name']}: {s['score']}")
    
    print("\n按成绩降序:")
    sorted_students_desc = quick_sort_custom(students, key=lambda x: x["score"], reverse=True)
    for s in sorted_students_desc:
        print(f"  {s['name']}: {s['score']}")
    
    # 性能测试
    print("\n\n📋 性能测试 (10000个随机整数):")
    print("-" * 60)
    
    large_array = [random.randint(0, 100000) for _ in range(10000)]
    
    print(f"{'算法':25} | {'耗时':>10} | {'正确性':>6}")
    print("-" * 60)
    
    benchmark(quick_sort, large_array, "标准快排")
    benchmark(quick_sort_randomized, large_array, "随机快排")
    benchmark(quick_sort_3way, large_array, "三向快排")
    benchmark(sorted, large_array, "Python内置排序")
    
    # 大量重复元素测试
    print("\n\n📋 大量重复元素测试 (10000个元素，范围0-10):")
    print("-" * 60)
    
    duplicate_array = [random.randint(0, 10) for _ in range(10000)]
    
    print(f"{'算法':25} | {'耗时':>10} | {'正确性':>6}")
    print("-" * 60)
    
    benchmark(quick_sort, duplicate_array, "标准快排")
    benchmark(quick_sort_randomized, duplicate_array, "随机快排")
    benchmark(quick_sort_3way, duplicate_array, "三向快排")
    benchmark(sorted, duplicate_array, "Python内置排序")
    
    print("\n" + "=" * 60)
    print("  测试完成！")
    print("=" * 60)
