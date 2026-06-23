# 快速排序算法实现

def quick_sort(arr):
    """
    快速排序算法
    
    Args:
        arr: 待排序的列表
    
    Returns:
        排序后的列表
    """
    if len(arr) <= 1:
        return arr
    
    pivot = arr[len(arr) // 2]  # 选择中间元素作为基准
    left = [x for x in arr if x < pivot]   # 小于基准的元素
    middle = [x for x in arr if x == pivot]  # 等于基准的元素
    right = [x for x in arr if x > pivot]  # 大于基准的元素
    
    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """
    原地快速排序算法（不需要额外空间）
    
    Args:
        arr: 待排序的列表
        low: 起始索引
        high: 结束索引
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        pivot_index = partition(arr, low, high)
        quick_sort_inplace(arr, low, pivot_index - 1)
        quick_sort_inplace(arr, pivot_index + 1, high)


def partition(arr, low, high):
    """
    分区函数
    
    Args:
        arr: 列表
        low: 起始索引
        high: 结束索引
    
    Returns:
        基准元素的最终位置
    """
    pivot = arr[high]  # 选择最后一个元素作为基准
    i = low - 1
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def demo():
    """
    演示快速排序算法
    """
    print("快速排序算法演示")
    print("=" * 40)
    
    # 测试用例1：基本测试
    test1 = [3, 6, 8, 10, 1, 2, 1]
    print(f"原数组: {test1}")
    print(f"排序后: {quick_sort(test1)}")
    print()
    
    # 测试用例2：随机数组
    import random
    test2 = [random.randint(1, 100) for _ in range(10)]
    print(f"随机数组: {test2}")
    print(f"排序后: {quick_sort(test2)}")
    print()
    
    # 测试用例3：已排序数组
    test3 = [1, 2, 3, 4, 5]
    print(f"已排序数组: {test3}")
    print(f"排序后: {quick_sort(test3)}")
    print()
    
    # 测试用例4：逆序数组
    test4 = [5, 4, 3, 2, 1]
    print(f"逆序数组: {test4}")
    print(f"排序后: {quick_sort(test4)}")
    print()
    
    # 测试用例5：包含重复元素
    test5 = [3, 3, 1, 1, 2, 2]
    print(f"包含重复元素: {test5}")
    print(f"排序后: {quick_sort(test5)}")
    print()
    
    # 演示原地排序
    print("原地快速排序演示")
    test6 = [10, 7, 8, 9, 1, 5]
    print(f"原数组: {test6}")
    quick_sort_inplace(test6)
    print(f"排序后: {test6}")


if __name__ == "__main__":
    demo()