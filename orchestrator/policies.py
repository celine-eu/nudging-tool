def can_send_today(sent_today: int, max_per_day: int) -> bool:
    return sent_today < max_per_day
