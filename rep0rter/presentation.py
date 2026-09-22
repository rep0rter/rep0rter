"""Public presentation credits and provenance, reviewed against the project note.

Names and contributions follow the 2026-09-19 HackMD note. A contribution
credit does not imply that an integration is deployed or a formal partnership.
"""

PROJECT_NOTE = 'https://hackmd.io/@erikka22/rJYsKhsKMg'

TEAM = [
    {'name': name, 'role': role, 'url': PROJECT_NOTE}
    for name, role in (
        ('Sky Hong', 'CI / Infra'),
        ('PGpenguin72', 'UI Design'),
        ('Natsumi', 'Code for Japan Slack integration tracking'),
        ('Erika', 'Translation improvements'),
        ('aoi', 'Code for Japan Notion content'),
        ('boyce', 'Threads connection work'),
        ('Han', 'Bug fixes and Korean translation'),
        ('Seo hyun', 'Advising'),
    )
]

SOURCES = [
    {'name': 'Project notes', 'url': PROJECT_NOTE, 'detail': 'What We Built on 2026-09-19'},
    {'name': 'g0v', 'url': 'https://g0v.tw/', 'detail': 'Taiwan civic-tech community'},
    {'name': 'Code for Japan', 'url': 'https://www.code4japan.org/', 'detail': 'Official community name'},
    {'name': 'Code for Korea', 'url': 'https://codefor.kr/', 'detail': '코드포코리아'},
]
