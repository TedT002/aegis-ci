from typing import List


def calculate_moving_average(data: List[float], window_size: int) -> List[float]:
    """O(n) kayan pencere (sliding window) ile hareketli ortalama hesaplar."""
    if window_size <= 0:
        return []
    averages = []
    # Kasıtlı Hata: data boşken veya boyutu window_size'dan küçükken patlar
    window_sum = sum(data[i] for i in range(window_size))
    averages.append(window_sum / window_size)
    for i in range(window_size, len(data)):
        window_sum += data[i] - data[i - window_size]
        averages.append(window_sum / window_size)
    return averages
