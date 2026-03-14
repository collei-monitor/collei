"""集中导入所有模型，供 Alembic 和应用启动使用."""

from app.db.base_class import Base  # noqa: F401

# ── Auth & Users ──
from app.models.auth import (  # noqa: F401
    LoginAttempt,
    OAuthState,
    OIDCProvider,
    Session,
    User,
)

# ── Clients ──
from app.models.clients import (  # noqa: F401
    Group,
    Server,
    ServerBillingRule,
    ServerGroup,
    ServerStatus,
)

# ── Monitoring ──
from app.models.monitoring import LoadNow, TrafficHourlyStat  # noqa: F401

# ── Network Monitoring ──
from app.models.network import (  # noqa: F401
    NetworkStatus,
    NetworkTarget,
    NetworkTargetDispatch,
)

# ── Configs ──
from app.models.config import Config  # noqa: F401

# ── Notification & Alerts ──
from app.models.notification import (  # noqa: F401
    AlertChannel,
    AlertHistory,
    AlertRule,
    AlertRuleChannelLink,
    AlertRuleTarget,
    Log,
    MessageSenderProvider,
)
