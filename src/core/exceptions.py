from fastapi import HTTPException

class CostLimitExceededError(HTTPException):
    def __init__(self, user_cost: float, cost_limit: float):
        super().__init__(
            status_code=402,  # Payment Required
            detail={
                "error": "cost_limit_exceeded",
                "message": "Cost limit exceeded. Please contact jaidenreddy@gmail.com or vivekvajipey@gmail.com, or Venmo @Jaiden-Reddy to increase your limit.",
                "user_cost": user_cost,
                "cost_limit": cost_limit
            }
        )
