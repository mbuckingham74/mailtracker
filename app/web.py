from fastapi.templating import Jinja2Templates

from .proxy_detection import detect_proxy_type
from .time_utils import to_local

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["detect_proxy_type"] = detect_proxy_type
templates.env.globals["to_local"] = to_local
