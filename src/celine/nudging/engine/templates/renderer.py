from jinja2 import Environment, Template as JinjaTemplate

# The plain body is delivered as text (push, text/plain e-mail): nothing is escaped.
# The HTML alternative is markup a mail client runs, so every value is escaped.
_html_env = Environment(autoescape=True)


def render(title_jinja: str, body_jinja: str, ctx: dict) -> tuple[str, str]:
    title = JinjaTemplate(title_jinja).render(**ctx)
    body = JinjaTemplate(body_jinja).render(**ctx)
    return title, body


def render_html(html_jinja: str | None, ctx: dict) -> str | None:
    if not html_jinja:
        return None
    return _html_env.from_string(html_jinja).render(**ctx)
