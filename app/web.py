from fastapi.templating import Jinja2Templates

from .paths import TEMPLATES_DIR
from .time_utils import to_local

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["to_local"] = to_local
