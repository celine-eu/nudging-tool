from celine.nudging.api.routes.admin.ingest import router as ingest
from celine.nudging.api.routes.admin.notifications import router as notifications
from celine.nudging.api.routes.admin.webpush import router as webpush

admin_routers = [
    ingest,
    notifications,
    webpush,
]
