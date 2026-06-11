"""
Shared HTTP GET with retry — the one retry policy for every API client.

Previously four near-identical implementations existed (fdic_client, sod_client
verbatim copy, sec_client inline loop, fmp_client ad hoc 429-once) with three
different policies — and the most critical fetch of all, SEC companyfacts,
had no retry at all.

Policy:
  • up to ``max_attempts`` total attempts
  • 429: honor Retry-After when parseable (capped 30s), else exponential
    backoff + jitter, then retry
  • connection errors / timeouts: exponential backoff + jitter, then retry
  • any other HTTP error: raise immediately (a 404 won't fix itself)
"""
import random
import time

import requests


def get_with_retry(url: str, params: dict | None = None,
                   headers: dict | None = None, timeout: int = 15,
                   max_attempts: int = 3) -> requests.Response | None:
    """GET with backoff. Returns the Response, or None if every attempt was
    eaten by 429s. Raises on non-429 HTTP errors and on the final
    connection/timeout failure."""
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                try:
                    wait = float(resp.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                wait = wait or ((2 ** attempt) + random.uniform(0, 1))
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError:
            raise  # non-429 HTTP errors aren't retried
        except (requests.ConnectionError, requests.Timeout):
            if attempt == max_attempts - 1:
                raise
            time.sleep((2 ** attempt) + random.uniform(0, 1))
    return None
