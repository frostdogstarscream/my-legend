from my_legend_production.render import caption_pages


class Font:
    def getlength(self,text):
        return len(text)*48


def test_long_caption_keeps_all_characters_with_bounded_visible_lines():
    text='对白：'+('雨后的院子里，两人终于把误会说开。'*12)
    pages=caption_pages(text,Font())
    assert len(pages)>1
    assert ''.join(line for page in pages for line in page)==text
    assert all(1<=len(page)<=3 for page in pages)
    assert all(Font().getlength(line)<=1190 for page in pages for line in page)


def test_short_caption_stays_on_one_page():
    assert caption_pages('旁白：院子安静下来。',Font())==[['旁白：院子安静下来。']]
