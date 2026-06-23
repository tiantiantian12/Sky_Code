"""
二分查找算法实现
"""


def binary_search(arr, target):
    """
    二分查找（迭代版本）
    :param arr: 已排序的数组
    :param target: 要查找的目标值
    :return: 目标值的索引，未找到返回 -1
    """
    left, right = 0, len(arr) - 1

    while left <= right:
        mid = left + (right - left) // 2

        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return -1


def binary_search_recursive(arr, target, left=0, right=None):
    """
    二分查找（递归版本）
    :param arr: 已排序的数组
    :param target: 要查找的目标值
    :param left: 左边界
    :param right: 右边界
    :return: 目标值的索引，未找到返回 -1
    """
    if right is None:
        right = len(arr) - 1

    # 基础情况
    if left > right:
        return -1

    mid = left + (right - left) // 2

    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, right)
    else:
        return binary_search_recursive(arr, target, left, mid - 1)


def demo():
    """演示函数"""
    print("二分查找算法演示")
    print("=" * 40)

    # 测试数组（必须是已排序的）
    arr = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
    print(f"\n已排序数组: {arr}")

    # 测试迭代版本
    print("\n--- 迭代版本 ---")
    targets = [23, 56, 1, 91, 38, 100]
    for target in targets:
        result = binary_search(arr, target)
        if result != -1:
            print(f"查找 {target}: 找到，索引为 {result}")
        else:
            print(f"查找 {target}: 未找到")

    # 测试递归版本
    print("\n--- 递归版本 ---")
    for target in targets:
        result = binary_search_recursive(arr, target)
        if result != -1:
            print(f"查找 {target}: 找到，索引为 {result}")
        else:
            print(f"查找 {target}: 未找到")

    # 边界测试
    print("\n--- 边界测试 ---")
    print(f"查找第一个元素 {arr[0]}: 索引 = {binary_search(arr, arr[0])}")
    print(f"查找最后一个元素 {arr[-1]}: 索引 = {binary_search(arr, arr[-1])}")

    # 空数组测试
    print(f"\n查找空数组: 索引 = {binary_search([], 5)}")


if __name__ == "__main__":
    demo()
