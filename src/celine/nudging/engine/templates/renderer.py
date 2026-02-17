from jinja2 import Template as JinjaTemplate

def render(title_jinja: str, body_jinja: str, ctx: dict) -> tuple[str, str]:
    title = JinjaTemplate(title_jinja).render(**ctx)
    body = JinjaTemplate(body_jinja).render(**ctx)
    return title, body
