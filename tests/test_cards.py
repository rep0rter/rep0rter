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
