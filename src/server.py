import sys
import logging
import tomllib

import httpx
import orjson
import sanic.response
from sanic import Sanic
from npf_renderer.utils import BASIC_LAYOUT_CSS

from . import routes
from . import privblur_extractor
from .helpers import setup_logging, helpers
from .version import VERSION, CURRENT_COMMIT

TumblrAPI = privblur_extractor.TumblrAPI

setup_logging.setup_logging(logging.WARN)

app = Sanic("Privblur", loads=orjson.loads, dumps=orjson.dumps, env_prefix='PRIVBLUR_')
app.config.TEMPLATING_PATH_TO_TEMPLATES = "src/templates"

app.ctx.LOGGER = logging.getLogger("privblur")
app.ctx.CURRENT_COMMIT = CURRENT_COMMIT  # Used for cache busting
app.ctx.VERSION = VERSION

app.ctx.URL_HANDLER = helpers.url_handler
app.ctx.BLACKLIST_RESPONSE_HEADERS = ("access-control-allow-origin", "alt-svc", "server")

# Privblur behavior configs

try:
    with open(app.config.get("PRIVBLUR_CONFIG_LOCATION", "./config.toml"), "rb") as config_file:
        app.ctx.PRIVBLUR_CONFIG = tomllib.load(config_file)
except FileNotFoundError:
    print("Cannot find configuration file at \"./config.toml\". Did you mean to set a new location with the environmental variable \"PRIVBLUR_CONFIG_LOCATION\"?")
    sys.exit()

@app.listener("before_server_start")
async def initialize(app):
    privblur_backend = app.ctx.PRIVBLUR_CONFIG["privblur_backend"]

    app.ctx.TumblrAPI = await TumblrAPI.create(
        main_request_timeout=privblur_backend["main_response_timeout"],
        json_loads=orjson.loads
    )

    # We'll also have a separate HTTP client for images
    media_request_headers = TumblrAPI.DEFAULT_HEADERS
    del media_request_headers["authorization"]

    # TODO set pool size for image requests

    def create_image_client(url, timeout):
        return httpx.AsyncClient(base_url=url, headers=media_request_headers, http2=True, timeout=timeout)

    app.ctx.Media64Client = create_image_client(
        "https://64.media.tumblr.com",
        privblur_backend["image_response_timeout"]
    )

    app.ctx.Media49Client = create_image_client(
        "https://49.media.tumblr.com",
        privblur_backend["image_response_timeout"]
    )

    app.ctx.TumblrAssetClient = create_image_client(
        "https://assets.tumblr.com",
        privblur_backend["image_response_timeout"]
    )


@app.listener("main_process_start")
async def main_startup_listener(app):
    """Startup listener to notify of privblur startup"""
    # Are we able to print colored output? If so we do it.
    try:
        import colorful
    except ImportError:
        colorful = None

    if not colorful:
        print(f"Starting up Privblur version {VERSION}")
    else:
        print(f"{colorful.green('Launching up')} {colorful.cyan('Privblur')} "
              f"{colorful.bold(f'{VERSION}')}")


@app.get("/")
async def root(request):
    return sanic.response.text(VERSION)


@app.get("/assets/base-post-layout.css")
async def base_post_layout(request):
    return sanic.response.text(BASIC_LAYOUT_CSS, content_type="text/css")


@app.middleware("response")
async def before_all_routes(request, response):
    # https://github.com/iv-org/invidious/blob/master/src/invidious/routes/before_all.cr
    response.headers["x-xss-protection"] = "1; mode=block"
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "nosniff"

    response.headers["content-security-policy"] = '; '.join([
      "default-src 'none'",
      "script-src 'self'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "manifest-src 'self'",
      "media-src 'self'",
      "child-src 'self' blob:",
    ])


# Register all routes:
for route in routes.BLUEPRINTS:
    app.blueprint(route)

# Static assets
app.static("/assets", "assets")


if __name__ == "__main__":
    app.run(debug=True)

