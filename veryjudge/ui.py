"""Render the local operator page without adding a public HTTP endpoint."""
from pathlib import Path
import argparse

from jinja2 import Environment,FileSystemLoader,select_autoescape


def render_template(name, **context):
    root=Path(__file__).parent
    env=Environment(loader=FileSystemLoader(root/"templates"),autoescape=select_autoescape())
    if "javascript" not in context:
        context["javascript"]=(root/"static"/(Path(name).stem+".js")).read_text()
    return env.get_template(name).render(**context)


def render(output,api_url="http://127.0.0.1:8000"):
    content=render_template("operator.html",api_url=api_url)
    target=Path(output).resolve()
    target.write_text(content)
    return target


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="operator.html")
    parser.add_argument("--api-url",default="http://127.0.0.1:8000")
    args=parser.parse_args()
    print(render(args.output,args.api_url))
