SLA_CONFIG = {
    "critical": {
        "threshold_minutes": 15,
        "penalty_per_minute": 100,
        "reward_base": 750,
    },
    "high": {
        "threshold_minutes": 30,
        "penalty_per_minute": 50,
        "reward_base": 750,
    },
    "medium": {
        "threshold_minutes": 60,
        "penalty_per_minute": 25,
        "reward_base": 750,
    },
    "low": {
        "threshold_minutes": 120,
        "penalty_per_minute": 10,
        "reward_base": 600,
    },
}
