"""AWS Lambda entry point (zip deployment): `transaction_service.lambda_handler.handler`.

Mangum turns Function URL / API Gateway events into ASGI requests, so every FastAPI route works
unchanged. Settings come from the function's environment variables.

The app starts once per Lambda instance, here at import (the init phase): Mangum's own lifespan
support would start and stop it on every request, i.e. a new database pool and read-only check
each time. It runs on the event loop Mangum reuses for every request, so the pool stays usable.
Lambda freezes the instance between requests and ends it without a shutdown hook; the pool's
connections simply close with it.
"""

import asyncio

from mangum import Mangum

from transaction_service.main import create_app

app = create_app()
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_loop.run_until_complete(app.router.lifespan_context(app).__aenter__())

handler = Mangum(app, lifespan="off")
