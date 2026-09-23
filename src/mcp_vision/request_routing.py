"""Route the user's goal independently of the surface used to invoke it.

Ask is allowed to gather information in a browser. It never authorizes writes.
Captured page text cannot select a route or supply missing task parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_PREFIX = re.compile(
    r"^\s*(?:(?:please|can you(?: help me(?: to)?)?|could you(?: help me(?: to)?)?|"
    r"would you(?: be able to)?|are you able to|will you|help me(?: to)?)\s+)*",
    re.I,
)
_CONTROLS = r"button|field|tab|menu|checkbox|control|form|input|dropdown|setting|link"
_FRESHNESS = re.compile(
    r"\b(?:now|currently|current|latest|newest|recent(?:ly)?|today|tonight|tomorrow|"
    r"yesterday|upcoming|next|this (?:week|month|quarter|year|season)|as of|still|"
    r"just announced|just released|when will|when does)\b",
    re.I,
)
_FLIGHT_ORIGIN = re.compile(
    r"\b(?:flights?|tickets?|trip)\s+(?:from\s+)?[a-z0-9][a-z0-9 .'-]*?\s+to\s+[a-z0-9]",
    re.I,
)


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
    # Freshness is a property of the request, not its subject. This sends any
    # time-sensitive real-world question to observed web evidence without a
    # growing list of companies, people, products, leagues, or events.
    if _FRESHNESS.search(text):
        return True
    return explicit or bool(re.search(
        r"\b(find|search|look for|fetch|retrieve|latest|current|today|weather|news|"
        r"flights?|inbox|unread|gmail|emails?|compare|prices?)\b", text)) or bool(
        re.search(r"\b(?:my|me|i)\b.*\b(?:github|calendar|drive|notion|slack|outlook|icollege)\b", text))


@dataclass(frozen=True)
class RequestRoute:
    kind: str  # context, browser, browser_open, surface, native, input
    message: str = ''
    missing: str = ''
    action: str = ''
    value: str = ''


def route_request(request: str, mode: str) -> RequestRoute:
    # App launch is the only native command that must be resolved before an
    # actionable surface exists. In-app commands are handled by the general
    # observed-surface loop so new capabilities do not require a phrase parser.
    if mode != 'guide':
        from mcp_vision.native_apps import parse_intent
        intent = parse_intent(request)
        if intent is not None and intent.action == 'open_app':
            return RequestRoute('native', intent.summary, action=intent.action, value=intent.value)
    if mode == 'guide':
        return RequestRoute('surface')
    # Explicit Act still uses the read-only mission for information gathering.
    # A request to send/book/buy must never be disguised as a lookup.
    text = request_text(request)
    if re.fullmatch(r'(?:do|handle|solve|fix)\s+(?:something|anything)[?.!]*', text):
        return RequestRoute('input', 'What would you like me to work on? Point at it or describe the outcome you want.', 'goal')
    from mcp_vision.plan import product_mention
    if mode == 'act' and re.match(r'(?:open|go to|navigate to)\s+', text):
        destination = re.sub(r'^(?:open|go to|navigate to)\s+', '', _PREFIX.sub('', request.strip()), flags=re.I).strip()
        if product_mention(destination) or re.fullmatch(r'https?://\S+', destination):
            return RequestRoute('browser_open', destination)
    mutation = re.match(r'(send|submit|book|buy|delete|fill|full out|complete|create|make|write|attach|upload|change|click|open|navigate|go to|switch)\b', text)
    if re.match(r'(?:next|previous|prev)\s+(?:tab|window)\b', text):
        mutation = True
    if needs_browser(request) and not mutation:
        if re.fullmatch(r'(?:do\s+)?(?:some\s+)?research(?:\s+(?:for|on)\s+me)?[?.!]*', text):
            return RequestRoute('input', 'What topic would you like me to research?', 'topic')
        has_origin = bool(re.search(r'\bfrom\s+\S+', text) or _FLIGHT_ORIGIN.search(text))
        if re.search(r'\bflights?\b', text) and not has_origin:
            return RequestRoute('input', 'What city or airport are you flying from? Include your departure and return dates (or say one-way) so I can search Google Flights.', 'departure')
        has_date = re.search(
            r'\b(?:today|tomorrow|week|weekend|month|anytime|any\s+time|any\s+day|flexible|whenever|any\s+(?:dates?|days?|week|month)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
            r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|'
            r'\b(?:on\s+)?(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\b|\d{1,4}[-/]\d{1,2}', text)
        if re.search(r'\bflights?\b', text) and not has_date:
            return RequestRoute('input', 'What are your departure and return dates? You can say one-way, give a flexible range, or just say any dates and I will pick.', 'dates')
        return RequestRoute('browser')
    return RequestRoute('context' if mode == 'ask' else 'surface')
