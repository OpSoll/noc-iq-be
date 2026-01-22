SLA_CONFIG = {
    "critical": {
        "threshold_minutes": 15,
        "penalty_per_minute": 100.0,
        "reward_base": 750.0,
    },
    "high": {
        "threshold_minutes": 30,
        "penalty_per_minute": 50.0,
        "reward_base": 750.0,
    },
    "medium": {
        "threshold_minutes": 60,
        "penalty_per_minute": 25.0,
        "reward_base": 750.0,
    },
    "low": {
        "threshold_minutes": 120,
        "penalty_per_minute": 10.0,
        "reward_base": 600.0,
    },
}