from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .paths import TEMPLATES_DIR
from .time_utils import to_local

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["to_local"] = to_local


def render_template(
    request: Request,
    name: str,
    context: Optional[Dict[str, Any]] = None,
    **response_kwargs: Any,
):
    template_context = dict(context or {})
    return templates.TemplateResponse(request, name, template_context, **response_kwargs)
