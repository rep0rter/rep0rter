import io

from PIL import Image

from rep0rter.config import Config
from rep0rter.publishers.cards import CardRenderer, SIZE, env
from rep0rter.store import Container, Event


def example():
    return Event('slack:C:1', 'slack', 'message', 'slack:C', 1,
                 author_name='測試作者', text='一起開發 공공 데이터 Civic tech をつくろう\n' * 20,
                 url='https://example.test/message')


def test_fallback_multiscript_png_cache_and_changed_text(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(CardRenderer, '_page', lambda _: None)
    event = example()
    with CardRenderer(cfg) as renderer:
        card = renderer.render(event, None)
        with Image.open(card) as image:
            assert image.size == SIZE
            assert image.getbbox() is not None
        original_time = card.stat().st_mtime_ns
        assert renderer.render(event, None) == card
        assert card.stat().st_mtime_ns == original_time
        event.text = 'Updated message'
        assert renderer.render(event, None) != card


def test_corrupt_cache_regenerates(tmp_path, monkeypatch):
    monkeypatch.setattr(CardRenderer, '_page', lambda _: None)
    with CardRenderer(Config(data_dir=tmp_path)) as renderer:
        card = renderer.render(example(), None)
        card.write_bytes(b'broken')
        assert renderer.render(example(), None) == card
        with Image.open(card) as image:
            image.verify()


def test_public_identity_picture_is_rendered(tmp_path, monkeypatch):
    import rep0rter.publishers.cards as cards
    avatar = Image.new('RGB', (82, 82), 'red')
    buffer = io.BytesIO()
    avatar.save(buffer, 'PNG')
    monkeypatch.setattr(cards, 'identity_image', lambda *_: buffer.getvalue())
    monkeypatch.setattr(CardRenderer, '_page', lambda _: None)
    with CardRenderer(Config(data_dir=tmp_path)) as renderer:
        path = renderer.render(example(), Container('slack:C', 'slack', 'test'))
        with Image.open(path) as image:
            assert image.getpixel((100, 100)) == (255, 0, 0)


def test_html_card_does_not_execute_source_html():
    markup = env.get_template('card.html').render(author='<script>bad()</script>', original='<img src="http://internal/">',
                                                  source='test', channel='test', avatar='', initial='T', date='today', brand='r')
    assert '<script>bad()' not in markup
    assert '&lt;script&gt;' in markup
    assert '&lt;img' in markup


def report_post():
    from rep0rter.store import Post
    return Post('slack:C:1', 1, 10, '中文標題', '中文摘要', translations={
        'en': {'headline': 'Civic data workshop', 'summary': 'The community is planning a workshop.'},
    })


def test_report_card_uses_translated_copy_and_clear_attribution(tmp_path, monkeypatch):
    import rep0rter.publishers.cards as cards
    seen = {}
    monkeypatch.setattr(cards, 'identity_image', lambda *_: None)

    def capture(self, event, ctx, avatar, rendered, version='card-v1'):
        seen.update(ctx=ctx, html=rendered, version=version)
        return tmp_path / 'report.png'

    monkeypatch.setattr(CardRenderer, '_render_image', capture)
    event = example()
    event.meta['source_name'] = '不可直接當成英文介面'
    with CardRenderer(Config(data_dir=tmp_path)) as renderer:
        assert renderer.render_report(event, Container('slack:C', 'slack', 'test'), report_post()) == tmp_path / 'report.png'
    html = seen['html']
    assert 'ENGLISH REPORT · Summary by rep0rter' in html
    assert 'Source: 測試作者' in html
    assert 'g0v Slack · #test' in html
    assert 'Civic data workshop' in html
    assert 'The community is planning a workshop.' in html
    assert '中文標題' not in html
    assert '一起開發' not in html
    assert '不可直接當成英文介面' not in html
    assert 'ORIGINAL EXCERPT' not in html
    assert seen['version'] == 'report-card-en-v1'


def test_report_card_fails_closed_before_fetching_avatar(tmp_path, monkeypatch):
    import pytest
    import rep0rter.publishers.cards as cards

    def forbidden(*args):
        raise AssertionError('Must validate English before fetching an avatar')

    monkeypatch.setattr(cards, 'identity_image', forbidden)
    post = report_post()
    with CardRenderer(Config(data_dir=tmp_path)) as renderer:
        for translation in (None, {}, {'headline': 'Title'}, {'headline': ' ', 'summary': 'Text'},
                            {'headline': 123, 'summary': 'Text'}):
            post.translations = {'en': translation}
            with pytest.raises(ValueError, match='English'):
                renderer.render_report(example(), None, post)
        post = report_post()
        with pytest.raises(ValueError, match='English'):
            renderer.render_report(example(), None, post, language='ja')


def test_report_and_original_cards_have_separate_cache_and_avatars(tmp_path, monkeypatch):
    import rep0rter.publishers.cards as cards
    avatar = io.BytesIO()
    Image.new('RGB', (82, 82), 'red').save(avatar, 'PNG')
    monkeypatch.setattr(cards, 'identity_image', lambda *_: avatar.getvalue())
    monkeypatch.setattr(CardRenderer, '_page', lambda _: None)
    post = report_post()
    with CardRenderer(Config(data_dir=tmp_path)) as renderer:
        original = renderer.render(example(), None)
        report = renderer.render_report(example(), None, post)
        assert original != report
        with Image.open(report) as image:
            assert image.size == SIZE
            assert image.getpixel((100, 100)) == (255, 0, 0)
        original_time = report.stat().st_mtime_ns
        assert renderer.render_report(example(), None, post) == report
        assert report.stat().st_mtime_ns == original_time
        post.translations['en']['summary'] = 'The workshop has been postponed.'
        assert renderer.render_report(example(), None, post) != report
        assert renderer.render(example(), None) == original


def test_report_html_escapes_translated_copy():
    markup = env.get_template('report-card.html').render(
        author='<script>bad()</script>', headline='<img src="http://internal/">',
        summary='<b>untrusted</b>', source='GitHub', channel='test', avatar='', initial='T',
        date='today', brand='rep0rter')
    assert '<script>bad()' not in markup
    assert '&lt;script&gt;' in markup
    assert '&lt;img' in markup
    assert '&lt;b&gt;untrusted&lt;/b&gt;' in markup
