# -*- coding: utf-8 -*-
"""
api.py
Minimal client for the Perfex CRM Task Import module's bearer-token API
(modules/task_import/controllers/Task_import_api.php in the
taskimportperfexcrm module).

Same shape as PerfexCRMLibreOfficeExtension/pythonpath/perfex_tracker/api.py
(that extension's client was the template for this one) plus one addition
this app is built around: timesheets_bulk(). Unlike the other three clients,
this app never calls start_timer()/stop_timer()/get_timer_status() at all -
it tracks a whole session passively and pushes the aggregated result once,
at Stop, via timesheets_bulk().

Uses urllib from the standard library only - no third-party dependency,
matching this project's other Python clients and the reference
today.otek.currency_converter Flathub app's own stdlib-only approach.
"""

import json
import urllib.request
import urllib.error
import urllib.parse

TIMEOUT_SECONDS = 10


class PerfexError(Exception):
    """Raised for any failed Perfex API call. `.code` is the machine
    -readable error code from the JSON body (or None for a network-level
    failure) and `.message` is the best human-readable explanation we
    could extract."""

    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _api_base(base_url):
    return base_url.rstrip("/") + "/index.php/task_import/task_import_api"


def _request(method, path, base_url, token=None, body=None, query=None):
    url = _api_base(base_url) + path
    if query:
        qs = "&".join(
            "%s=%s" % (k, urllib.parse.quote(str(v)))
            for k, v in query.items()
            if v is not None
        )
        if qs:
            url = url + "?" + qs

    data = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = urllib.parse.urlencode(
            {k: v for k, v in body.items() if v is not None}
        ).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            raise PerfexError(
                "Perfex API error (%d): %s" % (e.code, e.reason), status=e.code
            )
        raise PerfexError(
            _message_with_detail(parsed) or "Perfex API error (%d)" % e.code,
            code=parsed.get("code"),
            status=e.code,
        )
    except urllib.error.URLError as e:
        raise PerfexError("Could not reach the CRM: %s" % e.reason, status=None)

    if parsed.get("status") != "success":
        raise PerfexError(
            _message_with_detail(parsed) or "Request failed.", code=parsed.get("code")
        )

    return parsed


def _message_with_detail(parsed):
    """Task_import_api.php attaches a "detail" field (the underlying
    exception's class + message) to server_error responses, since by the
    time that code path runs the caller has already proven a valid
    password or bearer token - surfacing it here saves a trip to the
    server's log files while this API is still being brought up."""
    message = parsed.get("message") or ""
    detail = parsed.get("detail")
    if detail:
        return (message + " (" + detail + ")") if message else detail
    return message


def login(base_url, email, password, device_name=None):
    """Returns {"token", "expires_at", "staff": {...}} on success. Raises
    PerfexError (e.g. code="invalid_credentials") otherwise. The password
    is only ever held in memory for the duration of this call.

    Uses PUT rather than POST: Perfex core has site-wide CSRF protection
    that CodeIgniter only enforces on a literal POST request, and core
    can't be edited to exclude this route. That check is a same-origin
    cookie guard anyway, irrelevant to this bearer-token API, so PUT
    simply sidesteps it without weakening anything (see the matching
    comment on Task_import_api::body() server-side)."""
    return _request(
        "PUT",
        "/login",
        base_url,
        body={"email": email, "password": password, "device_name": device_name},
    )


def logout(base_url, token):
    return _request("PUT", "/logout", base_url, token=token)


def list_projects(base_url, token):
    return _request("GET", "/projects", base_url, token=token).get("projects", [])


def list_tasks(base_url, token, project_id):
    return _request(
        "GET", "/tasks", base_url, token=token, query={"project_id": project_id}
    ).get("tasks", [])


def timesheets_bulk(base_url, token, task_id, entries):
    """Pushes a batch of already-aggregated {start_time, end_time, note}
    entries as one call, each becoming its own tbltaskstimers row against
    task_id. `entries` is a list of dicts; encoded to a JSON string in the
    request body since the server's generic form-body parser only handles
    scalar fields directly (see Task_import_api::validate_bulk_entries()).
    Returns the parsed response dict: {inserted, skipped,
    total_duration_seconds, timer_ids}."""
    return _request(
        "PUT",
        "/timesheets_bulk",
        base_url,
        token=token,
        body={"task_id": task_id, "entries": json.dumps(entries)},
    )
