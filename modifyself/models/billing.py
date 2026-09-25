"""
Billing models (payment sources, etc.)
"""

from typing import Optional, Dict, Any
from .base import DiscordObject

class PaymentSource(DiscordObject):
    """Represents a payment source (card, PayPal, etc.)."""
    
    __slots__ = (
        "type",
        "brand",
        "last_4",
        "expires_month",
        "expires_year",
        "default",
        "invalid",
        "billing_address",
    )
    
    def __init__(self, *, state, data: dict):
        super().__init__(state=state, data=data)
        self._update(data)
    
    def _update(self, data: dict):
        self.type = data.get("type")
        self.brand = data.get("brand")
        self.last_4 = data.get("last_4")
        self.expires_month = data.get("expires_month")
        self.expires_year = data.get("expires_year")
        self.default = data.get("default", False)
        self.invalid = data.get("invalid", False)
        self.billing_address = data.get("billing_address")
    
    @property
    def is_card(self) -> bool:
        return self.type == 1
    
    @property
    def is_paypal(self) -> bool:
        return self.type == 2
    
    def __repr__(self) -> str:
        return f"<PaymentSource id={self.id} type={self.type} last4={self.last_4}>"

class Subscription(DiscordObject):
    """Represents a subscription (Nitro, etc.)."""
    
    __slots__ = (
        "type",
        "plan_id",
        "status",
        "current_period_start",
        "current_period_end",
        "renewal_interval",
        "renewal_interval_count",
        "entitlement_id",
    )
    
    def __init__(self, *, state, data: dict):
        super().__init__(state=state, data=data)
        self._update(data)
    
    def _update(self, data: dict):
        self.type = data.get("type")
        self.plan_id = data.get("plan_id")
        self.status = data.get("status")
        self.current_period_start = data.get("current_period_start")
        self.current_period_end = data.get("current_period_end")
        self.renewal_interval = data.get("renewal_interval")
        self.renewal_interval_count = data.get("renewal_interval_count")
        self.entitlement_id = data.get("entitlement_id")
    
    @property
    def is_nitro(self) -> bool:
        return self.type == 1
    
    @property
    def is_nitro_classic(self) -> bool:
        return self.type == 2
    
    @property
    def is_active(self) -> bool:
        return self.status == 1
    
    def __repr__(self) -> str:
        return f"<Subscription id={self.id} type={self.type} status={self.status}>"