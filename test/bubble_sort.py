def bubble_sort(arr):
    """冒泡排序函数
    
    通过相邻元素比较和交换，将最大的元素逐步"冒泡"到数组末端
    时间复杂度：平均 O(n²)，最坏 O(n²)
    """
    n = len(arr)
    
    for i in range(n):
        # 标志位，如果某轮没有交换说明已经有序
        swapped = False
        
        # 每轮冒泡后，最大的元素已到末端，所以只需比较前 n-i-1 个
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                # 交换相邻元素
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swapped = True
        
        # 如果没有发生交换，数组已有序，可以提前结束
        if not swapped:
            break
    
    return arr

if __name__ == "__main__":
    # 测试示例
    arr = [64, 34, 25, 12, 22, 11, 90]
    print(bubble_sort(arr.copy()))  # 输出: [11, 12, 22, 25, 34, 64, 90]
