#!/usr/bin/env python3
"""Seed and serve a local design-review preview with fictional content only.

  PYTHONPATH=<checkout> python scripts/design-review/preview.py seed --data /tmp/review/data
  PYTHONPATH=<checkout> python scripts/design-review/preview.py serve --data /tmp/review/data --port 8811

The seed never reads production data, calls a model, or contacts Telegram.
All people, projects and links are invented for screenshots. `serve` runs the
Flask account app with throwaway Google settings so signed-in forms render;
the Google button is never pressed by the capture script.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

NOW = time.time()
HOUR = 3600

# (source, container, author, hours ago, original text, extra meta, {lang: (headline, summary)})
STORIES = [
    ("slack", "civic-maps", "Lin Yu-ting", 3,
     "We just shipped offline tiles for the flood shelter map. Testers wanted this weekend! #opendata",
     {"take_part": "Join the Saturday test session in #civic-maps."},
     {"en": ("Flood shelter map now works offline",
             "The civic-maps team shipped offline map tiles so shelter locations stay readable when mobile data drops. Volunteers are invited to a Saturday test session."),
      "zh-TW": ("避難所地圖支援離線瀏覽",
                "civic-maps 團隊推出離線圖磚，行動網路中斷時仍能查看避難所位置。週六將舉辦測試聚會，歡迎志工參加。"),
      "ja": ("避難所マップがオフラインに対応",
             "civic-maps チームがオフライン地図タイルを公開し、通信が途切れても避難所の位置を確認できるようになりました。土曜日にテスト会を開きます。"),
      "ko": ("대피소 지도, 오프라인 지원 시작",
             "civic-maps 팀이 오프라인 지도 타일을 공개해 모바일 데이터가 끊겨도 대피소 위치를 볼 수 있게 됐습니다. 토요일 테스트 모임에 자원봉사자를 모집합니다.")}),
    ("github", "opencouncil/minutes", "opencouncil", 7,
     "Release v2.4.0: speaker search across 1,200 council meetings, CSV export, faster OCR.",
     {},
     {"en": ("Council minutes search adds speaker filter in v2.4",
             "OpenCouncil's v2.4.0 release lets readers search 1,200 meetings by speaker, export results as CSV, and processes scanned minutes faster."),
      "zh-TW": ("議會逐字稿搜尋 v2.4 新增發言人篩選",
                "OpenCouncil 發布 v2.4.0，可依發言人搜尋 1,200 場會議、匯出 CSV，掃描檔辨識也更快。"),
      "ja": ("議事録検索 v2.4、発言者で絞り込み可能に",
             "OpenCouncil の v2.4.0 では 1,200 件の会議を発言者で検索でき、CSV 出力と OCR の高速化も加わりました。"),
      "ko": ("의회 회의록 검색 v2.4, 발언자 필터 추가",
             "OpenCouncil v2.4.0은 1,200건의 회의를 발언자별로 검색하고 CSV로 내보낼 수 있으며 스캔 문서 처리도 빨라졌습니다.")}),
    ("mastodon", "@codeforkorea", "Code for Korea", 20,
     "다음 주 해커톤 주제는 대중교통 접근성입니다. 데이터셋과 멘토 명단을 공개했습니다.",
     {},
     {"en": ("Code for Korea picks transit accessibility for next hackathon",
             "Next week's hackathon will focus on public transit accessibility. Organizers published starter datasets and the mentor list."),
      "zh-TW": ("Code for Korea 下週黑客松聚焦大眾運輸無障礙",
                "主辦方公布下週黑客松主題為大眾運輸無障礙，並釋出入門資料集與導師名單。"),
      "ja": ("Code for Korea、次回ハッカソンは公共交通のアクセシビリティ",
             "来週のハッカソンは公共交通のアクセシビリティがテーマです。データセットとメンター一覧が公開されました。"),
      "ko": ("코드포코리아, 다음 해커톤 주제는 대중교통 접근성",
             "다음 주 해커톤은 대중교통 접근성을 다룹니다. 주최 측이 시작용 데이터셋과 멘토 명단을 공개했습니다.")}),
    ("rss", "Code for Japan Blog", "Code for Japan", 28,
     "シビックテックフォーラム2026の登壇者募集を開始しました。締切は11月20日です。",
     {"hashtags": ["civictech"]},
     {"en": ("Civic Tech Forum 2026 opens call for speakers",
             "Code for Japan is accepting talk proposals for Civic Tech Forum 2026 until November 20."),
      "zh-TW": ("2026 公民科技論壇開放講者徵稿",
                "Code for Japan 開始徵求 2026 公民科技論壇講者，截止日為 11 月 20 日。"),
      "ja": ("シビックテックフォーラム2026、登壇者を募集",
             "Code for Japan がシビックテックフォーラム2026の登壇提案を11月20日まで受け付けています。"),
      "ko": ("시빅테크 포럼 2026 연사 모집",
             "코드포재팬이 11월 20일까지 시빅테크 포럼 2026 발표 제안을 받습니다.")}),
    ("slack", "air-quality", "Chen Mei-hua", 34,
     "Sensor calibration notes are up. 18 new low-cost sensors joined the network in Tainan. #airquality",
     {},
     {"en": ("Tainan air-quality network grows by 18 sensors",
             "Volunteers added 18 low-cost sensors in Tainan and published calibration notes so others can compare readings."),
      "zh-TW": ("台南空品網新增 18 個感測器",
                "志工在台南新增 18 個低成本感測器，並公開校正筆記，方便比對數據。"),
      "ja": ("台南の大気観測網に18台のセンサーが追加",
             "ボランティアが台南に低コストセンサー18台を設置し、比較用の校正メモを公開しました。"),
      "ko": ("타이난 대기질 관측망, 센서 18대 추가",
             "자원봉사자들이 타이난에 저가 센서 18대를 설치하고 비교용 보정 노트를 공개했습니다.")}),
    ("slack", "civic-maps", "Lin Yu-ting", 52,
     "Draft schema for shelter capacity is open for comments until Friday.",
     {},
     {"en": ("Shelter capacity schema open for comments",
             "A draft data schema for shelter capacity is open for public comment until Friday."),
      "zh-TW": ("避難所容量資料格式開放討論",
                "避難所容量的資料格式草案開放留言，至週五為止。"),
      "ja": ("避難所の収容人数スキーマ案、意見募集中",
             "避難所の収容人数に関するデータ形式の草案に、金曜日まで意見を募集しています。"),
      "ko": ("대피소 수용 인원 스키마 의견 수렴",
             "대피소 수용 인원 데이터 형식 초안에 대해 금요일까지 의견을 받습니다.")}),
    ("github", "budget-viz/budget-viz", "budget-viz", 60,
     "Release v1.0: interactive treemap of the 2027 central government budget.",
     {},
     {"en": ("Budget visualizer reaches 1.0 with 2027 treemap",
             "The budget-viz project released version 1.0 with an interactive treemap of the 2027 central government budget."),
      "zh-TW": ("預算視覺化 1.0 推出 2027 年度樹狀圖",
                "budget-viz 發布 1.0 版，提供 2027 年度中央政府總預算的互動樹狀圖。"),
      "ja": ("予算可視化ツールが1.0に、2027年度ツリーマップ",
             "budget-viz が 1.0 を公開し、2027年度中央政府予算のインタラクティブなツリーマップを提供しました。"),
      "ko": ("예산 시각화 1.0, 2027년 트리맵 공개",
             "budget-viz가 2027년 중앙정부 예산 인터랙티브 트리맵을 담은 1.0 버전을 공개했습니다.")}),
    ("slack", "education", "Wang Hsiao-wen", 76,
     "Teachers' workshop on open data lesson plans: slides and recordings shared.",
     {},
     {"en": ("Open data lesson-plan workshop shares slides",
             "Slides and recordings from the teachers' open data lesson-plan workshop are now public."),
      "zh-TW": ("開放資料教案工作坊釋出簡報",
                "教師開放資料教案工作坊的簡報與錄影已公開。"),
      "ja": ("オープンデータ授業案ワークショップの資料を公開",
             "教員向けオープンデータ授業案ワークショップのスライドと録画が公開されました。"),
      "ko": ("오픈데이터 수업안 워크숍 자료 공개",
             "교사 대상 오픈데이터 수업안 워크숍의 발표 자료와 녹화본이 공개됐습니다.")}),
    ("mastodon", "@openstreetmap_tw", "OSM Taiwan", 100,
     "Mapping party recap: 640 building outlines added around Hualien.",
     {},
     {"en": ("Mapping party adds 640 buildings around Hualien",
             "OpenStreetMap Taiwan volunteers traced 640 building outlines around Hualien during last weekend's mapping party."),
      "zh-TW": ("圖資松在花蓮新增 640 棟建物",
                "OpenStreetMap 台灣志工在上週末的圖資松，於花蓮周邊描繪了 640 棟建物輪廓。"),
      "ja": ("マッピングパーティーで花蓮の建物640棟を追加",
             "OpenStreetMap 台湾のボランティアが先週末、花蓮周辺の建物640棟の輪郭を描きました。"),
      "ko": ("매핑 파티로 화롄 건물 640채 추가",
             "오픈스트리트맵 대만 자원봉사자들이 지난 주말 화롄 일대 건물 640채의 윤곽을 그렸습니다.")}),
    ("slack", "security", "Hsu Chia-hao", 130,
     "Reminder: rotate deploy keys for the shared hosting org by Monday.",
     {},
     {"en": ("Shared hosting projects asked to rotate deploy keys",
             "Maintainers of projects on the shared hosting organization should rotate their deploy keys by Monday."),
      "zh-TW": ("共用主機專案請於週一前更換部署金鑰",
                "使用共用主機組織的專案維護者，請在週一前更換部署金鑰。"),
      "ja": ("共有ホスティングのデプロイキー更新のお願い",
             "共有ホスティング組織のプロジェクト管理者は月曜日までにデプロイキーを更新してください。"),
      "ko": ("공유 호스팅 프로젝트, 배포 키 교체 요청",
             "공유 호스팅 조직의 프로젝트 관리자는 월요일까지 배포 키를 교체해야 합니다.")}),
    # English translation pending: default edition shows the explicit pending notice.
    ("slack", "general", "Tsai Pei-shan", 150,
     "大松報名表已開放，這次在高雄舉辦。",
     {},
     {"zh-TW": ("高雄大松開放報名", "本次大松移師高雄舉辦，報名表已開放。")}),
    ("rss", "Code for Japan Blog", "Code for Japan", 170,
     "自治体向けオープンデータ研修の第3期参加団体を発表しました。",
     {},
     {"en": ("Third cohort announced for municipal open data training",
             "Code for Japan announced the municipalities joining the third cohort of its open data training program."),
      "zh-TW": ("地方政府開放資料培訓第三期名單公布",
                "Code for Japan 公布第三期地方政府開放資料培訓的參與單位。"),
      "ja": ("自治体向けオープンデータ研修、第3期参加団体を発表",
             "Code for Japan が自治体向けオープンデータ研修第3期の参加団体を発表しました。")}),
]


PREVIEW_GOOGLE = dict(google_client_id="preview-only.apps.googleusercontent.com",
                      google_client_secret="preview-only", web_secret_key="preview-only-" + "0123456789abcdef" * 3)


def seed(data_dir: Path, *, port: int, empty: bool = False) -> None:
    from rep0rter.config import Config
    from rep0rter.publishers import site
    from rep0rter.store import Container, Event, Post, Store
    from rep0rter import stories

    os.environ.setdefault("REP0RTER_CARD_FONT", _card_font())
    cfg = Config(data_dir=data_dir, site_url=f"http://127.0.0.1:{port}",
                 card_font=os.environ["REP0RTER_CARD_FONT"], **PREVIEW_GOOGLE)
    data_dir.mkdir(parents=True, exist_ok=True)
    with Store(cfg.db_path) as store:
        stories.ensure(store)
        if not empty:
            for index, (source, name, author, hours, text, meta, editions) in enumerate(STORIES):
                container = Container(id=f"{source}:{name}", source=source, name=name)
                store.upsert_container(container)
                ts = NOW - hours * HOUR
                slug = name.strip("@").replace("/", "-").replace(" ", "-").lower()
                event = Event(id=f"{source}:{name}:{index}", source=source, kind="message",
                              container_id=container.id, ts=ts, author_id=f"author-{author}",
                              author_name=author, text=text,
                              url=f"https://example.org/{slug}/{index}",
                              reply_count=(index * 3) % 7, meta={"visibility": "public", **meta})
                store.upsert_events([event])
                origin = editions.get("zh-TW") or editions.get("en")
                post = Post(event_id=event.id, published_at=ts + 600, score=10 - index * 0.1,
                            headline=origin[0], summary=origin[1],
                            translations={lang: {"headline": h, "summary": s} for lang, (h, s) in editions.items()})
                post.id = store.add_post(post)
            # A community story with a superseded first revision.
            container = Container(id="community:stories", source="community", name="Community stories")
            store.upsert_container(container)
            first = second = None
            for revision, hours, date_text in ((1, 200, "November 14"), (2, 1.5, "November 21")):
                event = Event(id=f"community:story:{revision}", source="community", kind="message",
                              container_id=container.id, ts=NOW - hours * HOUR, author_id="author-Kuo",
                              author_name="Kuo Ya-chen", text=f"Disability-friendly venue guide sprint, {date_text}.",
                              url="https://example.org/venue-guide",
                              meta={"visibility": "public", "self_reported": True, "project_name": "Venue Guide",
                                    "take_part": "Bring a laptop; remote participation is welcome.",
                                    "hashtags": ["accessibility"]})
                store.upsert_events([event])
                editions = {
                    "en": (f"Venue accessibility guide sprint moves to {date_text}",
                           f"The Venue Guide team will hold its accessibility data sprint on {date_text}, collecting entrance, toilet and elevator details for event venues."),
                    "zh-TW": (f"場地無障礙指南衝刺改在 {date_text}",
                              f"Venue Guide 團隊將在 {date_text} 舉辦無障礙資料衝刺，收集活動場地的入口、廁所與電梯資訊。"),
                    "ja": (f"会場アクセシビリティガイドのスプリント、{date_text}に",
                           f"Venue Guide チームが {date_text} に、会場の入口・トイレ・エレベーター情報を集めるスプリントを開きます。"),
                    "ko": (f"행사장 접근성 가이드 스프린트, {date_text}로",
                           f"Venue Guide 팀이 {date_text}에 행사장 출입구·화장실·엘리베이터 정보를 모으는 스프린트를 엽니다."),
                }
                post = Post(event_id=event.id, published_at=NOW - hours * HOUR + 300, score=9,
                            headline=editions["en"][0], summary=editions["en"][1],
                            translations={lang: {"headline": h, "summary": s} for lang, (h, s) in editions.items()})
                post.id = store.add_post(post)
                if revision == 1:
                    first = post.id
                else:
                    second = post.id
            with store.conn:
                store.conn.execute("INSERT INTO stories VALUES (?, ?, ?)", ("venue", NOW, "community:story:1"))
                for revision, post_id in ((1, first), (2, second)):
                    store.conn.execute("INSERT INTO story_posts VALUES (?, ?, ?, ?, ?, ?)",
                                       ("venue", revision, post_id, f"fp{revision}", "[]", NOW))
            store.set_kv("collector_health", json.dumps({"healthy": True, "last_healthy_at": NOW}))
        # A withdrawn post id that has no remaining public content.
        from rep0rter import policy  # noqa: F401  (ensures retractions schema)
        store.conn.executescript(policy.SCHEMA if hasattr(policy, "SCHEMA") else "")
        with store.conn:
            store.conn.execute("INSERT OR IGNORE INTO retractions(post_id, requested_at, status) VALUES (9999, ?, 'complete')", (NOW,))
        site.build(store, cfg)
    print(f"seeded {data_dir}")


def _card_font() -> str:
    for candidate in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                      "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                      "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"):
        if Path(candidate).exists():
            return candidate
    import subprocess
    return subprocess.run(["fc-match", "-f", "%{file}", ":lang=zh-tw"], capture_output=True, text=True).stdout


def serve(data_dir: Path, port: int) -> None:
    from rep0rter.config import Config
    from rep0rter.store import Store
    from rep0rter import web
    from rep0rter.publishers import site
    os.environ.setdefault("REP0RTER_CARD_FONT", _card_font())
    cfg = Config(data_dir=data_dir, site_url=f"http://127.0.0.1:{port}",
                 card_font=os.environ["REP0RTER_CARD_FONT"], **PREVIEW_GOOGLE)
    if os.environ.get("PREVIEW_LOGIN_DISABLED"):
        cfg.google_client_id = None
    app = web.create_app(cfg)
    real_build = site.build
    fail_next = []

    def build(*args, **kwargs):
        # Preview-only fault injection for the "saved, but feed refresh failed" state.
        if fail_next:
            fail_next.clear()
            raise RuntimeError("preview build failure")
        return real_build(*args, **kwargs)
    site.build = build

    @app.get('/__preview/fail-next-build')
    def preview_fail_next_build():
        fail_next.append(True)
        return {'armed': True}

    # Preview-only session minting for screenshots of signed-in forms.
    @app.get('/__preview/sign-in')
    def preview_sign_in():
        import uuid
        from flask import redirect, request, session
        token = secrets.token_urlsafe(32)
        # A fresh account per visit keeps each captured state clear of the
        # five-posts-per-day limit that earlier preview submissions consume.
        account = uuid.uuid4().hex
        with Store(cfg.db_path) as store, store.conn:
            store.conn.execute("INSERT INTO project_accounts VALUES (?, ?, 'Kuo Ya-chen', ?)", (account, 'preview-' + account, NOW))
            store.conn.execute("INSERT INTO project_sessions VALUES (?, ?, ?)", (web._hash(token), account, time.time() + 86400))
        session.clear()
        session.permanent = True
        session['login_token'] = token
        return redirect(request.args.get('next', '/write'), code=303)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "seed-empty", "serve"])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8811)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.data, args.port)
    else:
        seed(args.data, port=args.port, empty=args.command == "seed-empty")


if __name__ == "__main__":
    sys.exit(main())
