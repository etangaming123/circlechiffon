"""The DX NET fetch paths added for smaller pages: musicSort rows, musicDetail
scores, idx lookup, URL builders and the image-cache rules. Markup copied from
live pages (idx values replaced); expected values read off that markup."""

from urllib.parse import parse_qs, urlsplit

from circlechiffon.adapters.maimai_net import urls
from circlechiffon.adapters.maimai_net.client import _img_cache_path
from circlechiffon.adapters.maimai_net.parser import (
    find_music_idx,
    parse_music_detail_scores,
    parse_music_records,
    parse_song_play_stats,
)
from circlechiffon.types import ChartType, ComboFlag, Difficulty, SyncFlag

_IMG = "https://maimaidx-eng.com/maimai-mobile/img/"

# record/musicSort/search/ row: one div, no outer p_r wrapper
_SORT_ROW = f"""<div class="music_master_score_back pointer w_450 m_15 p_3 f_0">
<form action="https://maimaidx-eng.com/maimai-mobile/record/musicDetail/" method="get">
<img src="{_IMG}diff_master.png" class="h_20 f_l">
<img src="{_IMG}music_dx.png" class="music_kind_icon f_r">
<div class="clearfix"></div>
<div class="music_lv_block f_r t_c f_14">12+</div>
<div class="music_name_block t_l f_13 break">キャットラビング</div>
<div class="music_score_block w_112 t_r f_l f_12">100.9821%</div>
<div class="music_score_block w_190 t_r f_l f_12"><img src="{_IMG}deluxscore.png" class="v_b f_l">
1,773 / 1,875
</div>
<img src="{_IMG}music_icon_sync.png?ver=1.65" class="h_30 f_r">
<img src="{_IMG}music_icon_ap.png?ver=1.65" class="h_30 f_r">
<img src="{_IMG}music_icon_sssp.png?ver=1.65" class="h_30 f_r">
<div class="clearfix"></div>
<input type="hidden" name="idx" value="IDX+A/B=">
</form></div>"""

# record/musicGenre/search/ row: outer wrapper, kind icon outside the form
_GENRE_ROW = f"""<div class="w_450 m_15 p_r f_0">
<div class="music_basic_score_back pointer p_3">
<form action="https://maimaidx-eng.com/maimai-mobile/record/musicDetail/" method="get">
<img src="{_IMG}diff_basic.png" class="h_20 f_l">
<div class="clearfix"></div>
<div class="music_lv_block f_r t_c f_14">5</div>
<div class="music_name_block t_l f_13 break">華天月兎</div>
<input type="hidden" name="idx" value="GENRE_IDX">
</form></div>
<img src="{_IMG}music_standard.png" class="music_kind_icon">
</div>"""

# record/musicDetail/: played EXPERT and MASTER only, no ADVANCED block
_DETAIL_BLOCK = """<div id="{diff}" class="music_{diff}_score_back w_450 m_15 p_3 f_0">
<img src="{img}diff_{diff}.png" class="h_20 f_l">
<img src="{img}music_dx.png" class="music_kind_icon f_r">
<div class="music_lv_back m_3 m_b_0 f_l t_c f_14">{lv}</div>
<div class="t_r f_r">
<img src="{img}music_icon_{rank}.png?ver=1.65" class="p_t_5 v_t">
<img src="{img}music_icon_{combo}.png?ver=1.65" class="h_45 v_t">
<img src="{img}music_icon_{sync}.png?ver=1.65" class="h_45 m_r_10 v_t">
<div class="black_block w_215 p_3 p_r_5 d_ib"><table class="collapse f_11"><tbody>
<tr><td>Last played date：</td><td>{played}</td></tr>
<tr><td>PLAY COUNT：</td><td>{count}</td></tr>
</tbody></table></div></div>
<div class="t_l">
<div class="music_score_block w_120 d_ib t_r f_12">{ach}</div>
<div class="music_score_block w_310 m_r_0 d_ib t_r f_12">
<img src="{img}deluxscore.png" class="v_m f_l">
{star}
{dx}
</div></div></div>"""
_DETAIL = "".join(
    _DETAIL_BLOCK.format(img=_IMG, **kw)
    for kw in (
        dict(diff="expert", lv="12", rank="sss", combo="fcp", sync="fdxp", played="2026/09/16 11:31",
             count="3", ach="100.1901%", dx="2,115 / 2,361",
             star=f'<img src="{_IMG}music_icon_dxstar_detail_1.png" class="w_80 p_l_10 f_r">'),
        dict(diff="master", lv="14", rank="d", combo="back", sync="sync", played="2026/05/13 12:52",
             count="1", ach="41.6201%", dx="896 / 2,664", star='<div class="w_80 f_r">　</div>'),
    )
)


def test_music_sort_row_parses_like_genre_row():
    (s,) = parse_music_records(_SORT_ROW)
    assert (s.title, s.chart_type, s.difficulty) == ("キャットラビング", ChartType.dx, Difficulty.master)
    assert (s.achievement, s.dx_score, s.dx_score_total, s.played) == (100.9821, 1773, 1875, True)
    assert (s.combo_flag, s.sync_flag, s.idx) == (ComboFlag.ap, SyncFlag.sync, "IDX+A/B=")


def test_both_layouts_on_one_page_each_row_once():
    scores = parse_music_records(_SORT_ROW + _GENRE_ROW)
    assert [(s.title, s.chart_type) for s in scores] == [("キャットラビング", ChartType.dx), ("華天月兎", ChartType.std)]


def test_find_music_idx_matches_title_and_type():
    page = _SORT_ROW + _GENRE_ROW
    assert find_music_idx(page, "華天月兎", ChartType.std) == "GENRE_IDX"
    assert find_music_idx(page, "華天月兎", ChartType.dx) is None
    assert find_music_idx(page, "missing", ChartType.dx) is None


def test_music_detail_scores_only_played_blocks():
    scores = parse_music_detail_scores(_DETAIL, "華天月兎")
    assert [s.difficulty for s in scores] == [Difficulty.expert, Difficulty.master]
    expert, master = scores
    assert (expert.title, expert.chart_type, expert.achievement) == ("華天月兎", ChartType.dx, 100.1901)
    assert (expert.dx_score, expert.dx_score_total, expert.combo_flag, expert.sync_flag) == (2115, 2361, ComboFlag.fcp, SyncFlag.fsdp)
    # no combo ("back" icon), blank star cell
    assert (master.achievement, master.dx_score, master.dx_score_total, master.combo_flag, master.sync_flag) == (
        41.6201, 896, 2664, None, SyncFlag.sync,
    )


def test_music_detail_same_page_gives_play_stats():
    stats = parse_song_play_stats(_DETAIL)
    assert set(stats) == {Difficulty.expert, Difficulty.master}
    assert stats[Difficulty.expert].play_count == 3 and stats[Difficulty.master].play_count == 1


def test_music_record_url_played_only_uses_music_sort():
    q = parse_qs(urlsplit(urls.music_record_url(3)).query)
    assert urlsplit(urls.music_record_url(3)).path.endswith("/record/musicSort/search/")
    assert q == {"search": ["A"], "sort": ["1"], "playCheck": ["on"], "diff": ["3"]}
    assert parse_qs(urlsplit(urls.music_record_url(3, genre=103)).query)["search"] == ["G-103"]


def test_music_record_url_utage_and_unplayed_use_music_genre():
    for url in (urls.music_record_url(urls.UTAGE_DIFF_VALUE), urls.music_record_url(0, played_only=False)):
        assert urlsplit(url).path.endswith("/record/musicGenre/search/")
        assert "playCheck" not in url
    assert parse_qs(urlsplit(urls.music_record_url(0, 105, played_only=False)).query)["genre"] == ["105"]
    assert parse_qs(urlsplit(urls.music_record_url(0, played_only=False)).query)["genre"] == ["99"]


def test_friend_score_url_genre():
    q = parse_qs(urlsplit(urls.friend_score_url("123", 3, 103)).query)
    assert q == {"scoreType": ["2"], "genre": ["103"], "diff": ["3"], "idx": ["123"]}
    assert parse_qs(urlsplit(urls.friend_score_url("123", 3)).query)["genre"] == ["99"]


def test_genre_ids_cover_every_non_utage_catalog_category():
    assert set(urls.GENRE_IDS.values()) == {101, 102, 103, 104, 105, 106}


def test_image_cache_rules():
    assert _img_cache_path(_IMG + "Icon/84b923fa041a1354.png") is not None
    assert _img_cache_path(_IMG + "rating_base_rainbow.png?ver=1.65") is not None
    # different ?ver= is a different file
    assert _img_cache_path(_IMG + "rating_base_rainbow.png?ver=1.66") != _img_cache_path(_IMG + "rating_base_rainbow.png?ver=1.65")
    assert _img_cache_path(_IMG + "photo/user/abcdef0123456789") is None
    assert _img_cache_path(_IMG + "photo/user/abc.png") is None
    assert _img_cache_path("https://example.com/maimai-mobile/img/Icon/x.png") is None
