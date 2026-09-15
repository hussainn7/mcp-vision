"""Task-local restrictions, independent of model and executor policy."""
from __future__ import annotations

import re
from dataclasses import dataclass


def normalized(text: str) -> str:
    return ' '.join(text.lower().replace('’', "'").split())


@dataclass(frozen=True)
class TaskConstraints:
    no_submit: bool = False
    no_send: bool = False
    no_delete: bool = False
    factual: bool = False
    show_only: bool = False
    stay_on_page: bool = False
    only_field: str = ''

    @classmethod
    def parse(cls, request, context=None):
        text = normalized(request)
        forbidden = lambda verb: bool(re.search(r"\b(?:do not|don't|never|without)\s+" + verb, text))
        field = re.search(r"only (?:change|fill|edit) (?:the )?[\"']?(.+?)[\"']?(?: field)?(?:[.!;]|$)", text)
        name = field.group(1).strip(' "\'') if field else ''
        if name in {'this', 'this field'}:
            target = (context.clicked_element or context.focused_element) if context else None
            name = target.name if target else '__unresolved__'
        return cls(no_submit=forbidden(r'submit\w*'), no_send=forbidden(r'send\w*'),
                   no_delete=forbidden(r'delet\w*'), factual='factual' in text or 'do not fabricate' in text,
                   show_only=bool(re.search(r'\b(just|only) show me\b', text)),
                   stay_on_page=forbidden(r'leave\w*') or 'stay on this page' in text,
                   only_field=name)

    def check(self, mode, action, target, *, source='', value=''):
        if mode != 'act' or self.show_only:
            raise PermissionError('This mode only reads the interface.')
        if action not in {'fill', 'select', 'set_checked', 'upload', 'click', 'scroll'}:
            raise PermissionError('Unsupported task action.')
        # Unknown click handlers can send, submit, delete, or navigate.
        if action == 'click' and (self.no_submit or self.no_send or self.no_delete or self.stay_on_page):
            raise PermissionError('This task forbids clicks with unknown side effects. Use a direct field operation.')
        if self.only_field and normalized(target.get('name', '')) != normalized(self.only_field):
            raise PermissionError('Only the selected field may change.')
        if self.no_send and action == 'upload':
            raise PermissionError('Attaching a file may send it to the site.')
        if self.factual and action in {'fill', 'select', 'set_checked'}:
            if not source or not str(value).strip() or normalized(str(value)) not in normalized(source):
                raise PermissionError('The value needs literal factual support in the selected source.')
