"""Stable, Unicode hashtags shared by submissions and generated timelines."""
from __future__ import annotations

import re
import unicodedata

MAX_TAGS = 8
MAX_LENGTH = 48


def normalize(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).casefold().lstrip('#')
    if not 1 <= len(value) <= MAX_LENGTH or not any(c.isalpha() for c in value):
        raise ValueError('Hashtags must contain a letter and be at most 48 characters.')
    if not all(c.isalnum() or c in '_-' or unicodedata.category(c).startswith('M') for c in value):
        raise ValueError('Use letters, numbers, hyphens or underscores in hashtags.')
    if not value[0].isalnum():
        raise ValueError('Start each hashtag with a letter or number.')
    return value


def parse(value: str) -> list[str]:
    tags = list(dict.fromkeys(normalize(part) for part in re.split(r'[\s,，]+', value.strip()) if part))
    if len(tags) > MAX_TAGS:
        raise ValueError('Choose up to eight hashtags.')
    return tags


def extract(text: str) -> list[str]:
    # URL fragments and numbered issues are not community hashtags.
    text = re.sub(r'https?://\S+', '', unicodedata.normalize('NFKC', text))
    tags = []
    for match in re.finditer(r'(?<![\w/#])#([^\s#.,!?;:()\[\]{}<>"\'，。！？、；：]+)', text):
        try:
            tag = normalize(match[1])
        except ValueError:
            continue
        if tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def for_story(event, container, original: str) -> list[str]:
    tags = []
    explicit = event.meta.get('hashtags', [])
    if isinstance(explicit, list):
        for value in explicit:
            if isinstance(value, str):
                try:
                    tags.append(normalize(value))
                except ValueError:
                    pass
    if event.source == 'slack' and container:
        try:
            tags.append(normalize(container.name))
        except ValueError:
            pass
    return list(dict.fromkeys(tags + extract(original)))[:MAX_TAGS]


def path(tag: str) -> str:
    return f'tags/{tag}/'
