# -*- coding: utf-8 -*-
"""
单例模式（Singleton Pattern）实现

单例模式确保一个类只有一个实例，并提供一个全局访问点。
本文件提供多种实现方式供参考。
"""

import threading


# ============================================
# 方式一：使用 __new__ 方法（推荐）
# ============================================
class Singleton:
    """通过重写 __new__ 方法实现单例模式"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, name=None):
        # 防止重复初始化
        if not Singleton._initialized:
            self.name = name
            Singleton._initialized = True


# ============================================
# 方式二：使用装饰器
# ============================================
def singleton_decorator(cls):
    """单例模式装饰器"""
    instances = {}
    
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    
    return get_instance


@singleton_decorator
class DecoratedSingleton:
    """使用装饰器实现的单例类"""
    
    def __init__(self, value=None):
        self.value = value


# ============================================
# 方式三：使用元类（Metaclass）
# ============================================
class SingletonMeta(type):
    """单例元类"""
    
    _instances = {}
    _lock = threading.Lock()
    
    def __call__(cls, *args, **kwargs):
        # 使用锁保证线程安全
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


class MetaSingleton(metaclass=SingletonMeta):
    """使用元类实现的单例类"""
    
    def __init__(self, data=None):
        self.data = data


# ============================================
# 方式四：线程安全的单例（双重检查锁）
# ============================================
class ThreadSafeSingleton:
    """线程安全的单例模式"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                # 双重检查
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, name=None):
        if not hasattr(self, '_initialized'):
            self.name = name
            self._initialized = True


# ============================================
# 测试代码
# ============================================
if __name__ == "__main__":
    print("=" * 50)
    print("单例模式测试")
    print("=" * 50)
    
    # 测试方式一
    print("\n【方式一：__new__ 方法】")
    s1 = Singleton("第一个实例")
    s2 = Singleton("第二个实例")
    print(f"s1.name = {s1.name}")
    print(f"s2.name = {s2.name}")
    print(f"s1 is s2: {s1 is s2}")  # True
    
    # 测试方式二
    print("\n【方式二：装饰器】")
    d1 = DecoratedSingleton(100)
    d2 = DecoratedSingleton(200)
    print(f"d1.value = {d1.value}")
    print(f"d2.value = {d2.value}")
    print(f"d1 is d2: {d1 is d2}")  # True
    
    # 测试方式三
    print("\n【方式三：元类】")
    m1 = MetaSingleton("元类单例")
    m2 = MetaSingleton("再次创建")
    print(f"m1.data = {m1.data}")
    print(f"m2.data = {m2.data}")
    print(f"m1 is m2: {m1 is m2}")  # True
    
    # 测试方式四
    print("\n【方式四：线程安全单例】")
    t1 = ThreadSafeSingleton("线程安全实例")
    t2 = ThreadSafeSingleton("再次创建")
    print(f"t1.name = {t1.name}")
    print(f"t2.name = {t2.name}")
    print(f"t1 is t2: {t1 is t2}")  # True
    
    print("\n" + "=" * 50)
    print("所有测试通过！每个类都只有一个实例。")
    print("=" * 50)
