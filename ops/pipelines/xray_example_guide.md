# Codebase X-ray: Flask

Sample report. Source: [pallets/flask](https://github.com/pallets/flask) at commit `49943b5` (version 3.2.0.dev), mapped 2026-10-30.
Files in this pack: `graph.html` (open in a browser, click any node), `GRAPH_REPORT.md` (raw graph stats), and this guide.

## The one-minute version

Flask is about 9,600 lines of Python in 25 files. Almost everything flows through four things: the `Flask` app object, the `AppContext` that wraps each request, the `Scaffold` base class that gives apps and blueprints their decorators, and the `Request`/`Response` wrappers around Werkzeug. If you read `app.py`, `ctx.py` and `sansio/scaffold.py` in that order, you understand most of the framework.

## Map of the code

| Area | Files | Lines | What lives there |
|---|---|---|---|
| App core | `app.py`, `sansio/app.py` | 2,700 | The `Flask` class and its I/O-free base `App`: routing, dispatch, error handling |
| Shared base | `sansio/scaffold.py` | 800 | `Scaffold`: `route`, `before_request`, `errorhandler` and friends, used by apps and blueprints |
| Blueprints | `blueprints.py`, `sansio/blueprints.py` | 820 | Reusable groups of routes that register onto an app later |
| Contexts and globals | `ctx.py`, `globals.py` | 620 | `AppContext`, `g`, `request`, `session`, `current_app` |
| HTTP wrappers | `wrappers.py` | 255 | `Request` and `Response`, subclasses of Werkzeug's |
| Sessions | `sessions.py` | 385 | Signed-cookie sessions and the interface to replace them |
| CLI | `cli.py` | 1,126 | The `flask` command, app discovery, `flask run` |
| Supporting | `config.py`, `helpers.py`, `templating.py`, `json/`, `testing.py`, `views.py`, `logging.py` | 2,500 | Config loading, `url_for`/`send_file`, Jinja integration, JSON, test client, class-based views |

## The core abstractions (most connected in the graph)

1. **`Flask`** (83 connections), `app.py`. The app object. Inherits from `App`, which inherits from `Scaffold`.
2. **`App`** (61), `sansio/app.py`. Everything about an app that doesn't do I/O.
3. **`setupmethod`** (50), `sansio/scaffold.py`. A decorator on every registration method. It raises an error if you add routes or hooks after the app has started serving requests.
4. **`AppContext`** (42), `ctx.py`. Pushed for every request and every CLI command.
5. **`Scaffold`** (37). The shared parent of `App` and `Blueprint`, which is why blueprints have the same decorators as apps.
6. **`Request`** (35) and **`Response`** (34), `wrappers.py`.
7. **`Blueprint`** (31). Records its setup calls and replays them when registered on an app.

## How a request flows through Flask

1. The WSGI server calls `Flask.__call__`, which calls `wsgi_app` (`app.py:1627`). Middleware should wrap `app.wsgi_app`, not `app`, so the app object stays reachable.
2. `wsgi_app` builds a context with `request_context(environ)` and pushes it. This makes `request`, `session`, `g` and `current_app` work.
3. `full_dispatch_request` (`app.py:1023`) sends the `request_started` signal, then runs `preprocess_request`, which calls every `before_request` hook. If a hook returns a value, that becomes the response and the view never runs.
4. `dispatch_request` (`app.py:993`) finds the view function for the matched URL rule and calls it.
5. Any exception goes to `handle_user_exception`, which looks up a registered `errorhandler`.
6. `finalize_request` turns the return value into a `Response` with `make_response`, then `process_response` runs the `after_request` hooks.
7. In a `finally` block, the context is popped. That runs `teardown_request` and then `teardown_appcontext` hooks, even when the request failed.

## Things that surprise new contributors

- **The "sans-io" split.** `src/flask/sansio/` holds code with no I/O and no Flask globals, so other implementations such as Quart can reuse it. When you look for a method on `Flask` and can't find it in `app.py`, it is in `sansio/app.py` or `sansio/scaffold.py`.
- **One context, not two.** As of 3.2, `RequestContext` merged into `AppContext`. The old name still works but warns, and will be removed in Flask 4.0. Many tutorials still describe two separate contexts.
- **Session loads lazily.** Since 3.2 the session is loaded on first access, not when the context is pushed.
- **Deprecations in flight.** `should_ignore_error` is deprecated (removal planned for 3.3), and dispatch methods now take `ctx` as their first argument; the old signatures go away in 4.0. Subclasses that override these methods need updating.
- **Import cycles by design.** The graph finds six 3- and 4-file cycles through `flask/__init__.py`, for example `__init__ -> app -> wrappers -> __init__`. They work because of import ordering. Moving imports around in these files can break startup.

## Where to make common changes

| You want to... | Start in |
|---|---|
| Change how URLs map to views | `sansio/scaffold.py` (`route`, `add_url_rule`) and `sansio/app.py` |
| Change request/response hooks | `app.py`: `preprocess_request`, `process_response` |
| Swap session storage | Subclass `SessionInterface` in `sessions.py`, set `app.session_interface` |
| Change error handling | `app.py`: `handle_user_exception`, `handle_exception` |
| Change the `flask` command | `cli.py` (`FlaskGroup`, `ScriptInfo`, `run_command`) |
| Change JSON behavior | `json/provider.py` (`DefaultJSONProvider`) |

## Suggested first week for a new engineer

1. Day 1: open `graph.html`, find `Flask`, and follow its links to `App` and `Scaffold`.
2. Day 2: step through one request in a debugger using the flow above, starting at `wsgi_app`.
3. Day 3: read `ctx.py` top to bottom and the `globals.py` proxies.
4. Day 4: read `sansio/blueprints.py` to see how deferred registration works.
5. Day 5: pick a small open issue that touches one of the areas in the map.

---
Map built with graphify (Apache-2.0, Graphify Labs). Guide written from the map and the source code.
