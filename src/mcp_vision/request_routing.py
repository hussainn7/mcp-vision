"""Route the user's goal independently of the surface used to invoke it.

Ask is allowed to gather information in a browser. It never authorizes writes.
Captured page text cannot select a route or supply missing task parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_PREFIX = re.compile(r"^\s*(?:(?:please|can you|could you|would you|help me(?: to)?)\s+)*", re.I)
_CONTROLS = r"button|field|tab|menu|checkbox|control|form|input|dropdown|setting|link"


def request_text(request: str) -> str:
    return _PREFIX.sub('', request.strip().lower().replace('’', "'"))


def needs_browser(request: str) -> bool:
    text = request_text(request)
    # A reference to the current surface stays local unless research is explicit.
    explicit = bool(re.search(r'\b(search (?:the )?(?:web|internet|google)|research|look up|google)\b', text))
    if re.search(r'\b(this (?:page|screen|error|text|document|field|button)|selected|highlighted|on (?:my |the )?screen)\b', text) and not explicit:
        return False
    if re.search(rf'\b(?:{_CONTROLS})\b', text) and not explicit:
        return False
    return explicit or bool(re.search(
        r"\b(find|search|look for|fetch|retrieve|latest|current|today|weather|news|"
        r"flights?|inbox|unread|gmail|emails?|compare|prices?)\b", text)) or bool(
        re.search(r"\b(?:my|me|i)\b.*\b(?:github|calendar|drive|notion|slack|outlook|icollege)\b", text))


@dataclass(frozen=True)
class RequestRoute:
    kind: str  # context, browser, browser_open, surface, input
    message: str = ''
    missing: str = ''


def route_request(request: str, mode: str) -> RequestRoute:
    if mode == 'guide':
        return RequestRoute('surface')
    # Explicit Act still uses the read-only mission for information gathering.
    # A request to send/book/buy must never be disguised as a lookup.
    text = request_text(request)
    from mcp_vision.plan import product_mention
    if mode == 'act' and re.match(r'(?:open|go to|navigate to)\s+', text):
        destination = re.sub(r'^(?:open|go to|navigate to)\s+', '', _PREFIX.sub('', request.strip()), flags=re.I).strip()
        if product_mention(destination) or re.fullmatch(r'https?://\S+', destination):
            return RequestRoute('browser_open', destination)
    mutation = re.match(r'(send|submit|book|buy|delete|fill|create|write|attach|upload|change|click|open|navigate|go to)\b', text)
    if needs_browser(request) and not mutation:
        if re.search(r'\bflights?\b', text) and not re.search(r'\bfrom\s+\S+', text):
            return RequestRoute('input', 'What city or airport are you flying from? Include your departure and return dates (or say one-way) so I can search Google Flights.', 'departure')
        has_date = re.search(
            r'\b(?:today|tomorrow|week|weekend|month|anytime|any\s+time|any\s+day|flexible|whenever|any\s+(?:dates?|days?|week|month)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
            r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|\d{1,4}[-/]\d{1,2}', text)
        if re.search(r'\bflights?\b', text) and not has_date:
            return RequestRoute('input', 'What are your departure and return dates? You can say one-way, give a flexible range, or just say any dates and I will pick.', 'dates')
        return RequestRoute('browser')
    return RequestRoute('context' if mode == 'ask' else 'surface')
