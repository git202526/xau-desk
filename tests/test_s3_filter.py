"""S3：关键位筛选（全部/支撑/阻力/强位）与窗口展开。"""
import pytest

ROW_JS = """() => {
  const rows = Array.from(document.querySelectorAll('#klist .kl:not(.now)'));
  const now = document.querySelector('#klist .kl.now .p');
  return {
    price: now ? parseFloat(now.textContent.replace(/,/g, '')) : null,
    rows: rows.map(r => ({
      p: parseFloat(r.querySelector('.p').textContent.replace(/,/g, '')),
      strong: !!r.querySelector('.w.strong'),
      label: r.querySelector('.n').textContent,
    })),
    meta: document.getElementById('klMeta').textContent,
    active: (document.querySelector('#klChips button.on') || {}).getAttribute
            ? document.querySelector('#klChips button.on').getAttribute('data-f') : null,
    rangeTxt: document.getElementById('klRange').textContent,
  };
}"""


@pytest.fixture
def page(make_env):
    env = make_env()
    env.page.click('.tabbar button[data-tab="struct"]')
    env.page.wait_for_timeout(250)
    return env.page


def rows_of(page):
    return page.evaluate(ROW_JS)


def test_filter_chips_exist(page):
    labels = page.eval_on_selector_all("#klChips button", "els => els.map(e => e.textContent)")
    assert labels == ["全部", "支撑", "阻力", "强位"], labels
    assert page.eval_on_selector("#klRange", "e => !!e") is True


def test_default_shows_near_window(page):
    r = rows_of(page)
    assert r["active"] == "all"
    assert "±2.5%" in r["meta"], r["meta"]
    assert r["rangeTxt"] == "看全部"
    assert len(r["rows"]) >= 5
    # 默认不应显示 12% 窗口才有的远端位
    assert all(abs(x["p"] / r["price"] - 1) < 0.026 for x in r["rows"]), r["rows"]


def test_support_filter_only_below_price(page):
    page.click('#klChips button[data-f="sup"]')
    page.wait_for_timeout(150)
    r = rows_of(page)
    assert r["active"] == "sup"
    assert r["rows"], "支撑筛选后不应为空"
    assert all(x["p"] < r["price"] for x in r["rows"]), r["rows"]


def test_resistance_filter_only_above_price(page):
    page.click('#klChips button[data-f="res"]')
    page.wait_for_timeout(150)
    r = rows_of(page)
    assert r["active"] == "res"
    assert r["rows"]
    assert all(x["p"] > r["price"] for x in r["rows"]), r["rows"]


def test_strong_filter_only_strong_rows(page):
    page.click('#klChips button[data-f="strong"]')
    page.wait_for_timeout(150)
    r = rows_of(page)
    assert r["active"] == "strong"
    assert r["rows"]
    assert all(x["strong"] for x in r["rows"]), r["rows"]


def test_expand_and_collapse_window(page):
    before = rows_of(page)
    page.click("#klRange")
    page.wait_for_timeout(200)
    after = rows_of(page)

    assert "±12%" in after["meta"], after["meta"]
    assert after["rangeTxt"] == "收起"
    assert len(after["rows"]) > len(before["rows"]), "展开后应显示更多位"
    assert max(abs(x["p"] / after["price"] - 1) for x in after["rows"]) > 0.026

    page.click("#klRange")
    page.wait_for_timeout(200)
    back = rows_of(page)
    assert back["rangeTxt"] == "看全部"
    assert len(back["rows"]) == len(before["rows"])


def test_filter_survives_tab_switch(page):
    page.click('#klChips button[data-f="sup"]')
    page.wait_for_timeout(150)
    page.click('.tabbar button[data-tab="ov"]')
    page.wait_for_timeout(200)
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(250)
    r = rows_of(page)
    assert r["active"] == "sup", "切 Tab 后筛选状态丢了"
    assert all(x["p"] < r["price"] for x in r["rows"])


def test_filtering_does_not_touch_cloud(make_env):
    env = make_env()
    page = env.page
    page.click('.tabbar button[data-tab="struct"]')
    page.wait_for_timeout(2600)      # 等首次价格写入落定
    base = env.gh.puts

    page.click('#klChips button[data-f="res"]')
    page.click("#klRange")
    page.wait_for_timeout(400)
    page.click("#klRange")
    page.click('#klChips button[data-f="all"]')
    page.wait_for_timeout(400)

    assert env.gh.puts == base, "筛选交互触发了云端写入：%s -> %s" % (base, env.gh.puts)


def test_rows_sorted_by_price(page):
    r = rows_of(page)
    prices = [x["p"] for x in r["rows"]]
    assert prices == sorted(prices, reverse=True), prices
