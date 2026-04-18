from fastapi import Request
from fastapi.templating import Jinja2Templates


def fmt_dt(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value in (None, ""):
        return "—"
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


def nl2br(value) -> str:
    if not value:
        return ""
    return str(value).replace("\n", "<br>")


def build_templates(directory: str = "templates") -> Jinja2Templates:
    templates = Jinja2Templates(directory=directory)
    templates.env.filters["dt"] = fmt_dt
    templates.env.filters["nl2br"] = nl2br
    templates.env.globals["len"] = len
    templates.env.globals["enumerate"] = enumerate
    return templates


def render_page(templates: Jinja2Templates, shared_context: dict, request: Request, template_name: str, **context):
    payload = dict(shared_context)
    payload.update({"request": request})
    payload.update(context)
    return templates.TemplateResponse(name=template_name, context=payload, request=request)
