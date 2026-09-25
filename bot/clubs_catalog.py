"""Каталог реальных клубов FC с лого (80 шт., взято из logovo-copy/src/ui.js
TEAM_LOGO_MAP). Лежат в logovo-copy/src/assets/logos/<файл>.png."""

LOGO_DIR = None  # резолвится в server.py: PROJECT_ROOT / "logovo-copy" / "src" / "assets" / "logos"

TEAM_LOGO_MAP: dict[str, str] = {
    'лидс': 'leeds.png', 'ренн': 'rennes.png', 'ницца': 'nice.png',
    'нэшвилл': 'nashville.png', 'порту': 'porto.png', 'вест хэм': 'west_ham.png',
    'вольфсбург': 'wolfsburg.png', 'фиорентина': 'fiorentina.png',
    'лацио': 'lazio.png', 'марсель': 'marseille.png', 'лилль': 'lille.png',
    'айнтрахт': 'eintracht.png', 'майнц': 'mainz.png', 'бернли': 'burnley.png',
    'будё глимт': 'bodo_glimt.png', 'кельн': 'koln.png',
    'вулверхэмптон': 'wolverhampton.png', 'бурирам': 'buriram.png',
    'валенсия': 'valencia.png', 'сельта': 'celta.png', 'ривер плейт': 'river_plate.png',
    'аякс': 'ajax.png', 'спортинг': 'sporting.png', 'монако': 'monaco.png',
    'бенфика': 'benfica.png', 'фулхэм': 'fulham.png', 'хоффенхайм': 'hoffenheim.png',
    'ланс': 'lens.png', 'аль-кадисия': 'al_qadsiah.png', 'торино': 'torino.png',
    'лос анджелес': 'los_angeles.png', 'псв': 'psv.png',
    'сандерленд': 'sunderland.png', 'ноттингем форест': 'nottingham_forest.png',
    'реал сосьедад': 'real_sociedad.png', 'париж': 'paris_fc.png',
    'фенербахче': 'fenerbahce.png', 'комо': 'como.png', 'брентфорд': 'brentford.png',
    'кристал пэлас': 'crystal_palace.png', 'аль-ахли': 'al_ahli.png', 'лион': 'lyon.png',
    'борнмут': 'bournemouth.png', 'аль-иттихад': 'al_ittihad.png',
    'трабзонспор': 'trabzonspor.png', 'вильярреал': 'villarreal.png',
    'штутгарт': 'stuttgart.png', 'болонья': 'bologna.png',
    'байя': 'bahia.png', 'милан': 'milan.png', 'боруссия дортмунд': 'borussia_dortmund.png',
    'интер милан': 'inter_milan.png', 'брайтон': 'brighton.png',
    'байер': 'bayer_leverkusen.png', 'лейпциг': 'leipzig.png', 'эвертон': 'everton.png',
    'аталанта': 'atalanta.png', 'астон вилла': 'aston_villa.png',
    'бешикташ': 'besiktas.png', 'интер майами': 'inter_miami.png', 'бетис': 'betis.png',
    'аль-хиляль': 'al_hilal.png', 'ньюкасл': 'newcastle.png',
    'атлетик бильбао': 'athletic_bilbao.png',
    'арсенал': 'arsenal.png', 'манчестер сити': 'manchester_city.png',
    'манчестер юнайтед': 'manchester_united.png', 'тоттенхэм': 'tottenham.png',
    'атлетико мадрид': 'atletico_madrid.png', 'барселона': 'barcelona.png',
    'реал мадрид': 'real_madrid.png', 'бавария': 'bayern.png',
    'ливерпуль': 'liverpool.png', 'челси': 'chelsea.png', 'наполи': 'napoli.png',
    'ювентус': 'juventus.png', 'рома': 'roma.png', 'псж': 'psg.png',
    'галатасарай': 'galatasaray.png', 'аль-наср': 'al_nassr.png',
}

# частые вводы → канон (расширяется по мере необходимости)
_ALIASES: dict[str, str] = {
    'реал': 'реал мадрид', 'сити': 'манчестер сити', 'юнайтед': 'манчестер юнайтед',
    'ман юнайтед': 'манчестер юнайтед', 'мю': 'манчестер юнайтед',
    'барса': 'барселона', 'барса.': 'барселона', 'мс': 'манчестер сити',
    'интер': 'интер милан', 'дортмунд': 'боруссия дортмунд', 'бд': 'боруссия дортмунд',
    'байер 04': 'байер', 'леверкузен': 'байер', 'спартак': None,  # нет в FC-каталоге
    'цска': None, 'зенит': None,
}

# названия форматов
FORMAT_NAMES = {'league': 'Лига', 'ucl': 'ЛЧ', 'uel': 'ЛЕ', 'uecl': 'ЛК'}
FORMAT_BY_INPUT = {'лига': 'league', 'league': 'league',
                   'лч': 'ucl', 'ucl': 'ucl',
                   'ле': 'uel', 'uel': 'uel',
                   'лк': 'uecl', 'uecl': 'uecl'}


def normalize_club_name(name: str) -> str:
    return ' '.join(name.lower().split())


_DISPLAY_SPECIAL = {'псв': 'ПСВ', 'псж': 'ПСЖ'}


def display_name(canon: str) -> str:
    """Ключи каталога строчные (для поиска); в БД и интерфейсе — «Аль-Хиляль», «Будё Глимт»."""
    if canon in _DISPLAY_SPECIAL:
        return _DISPLAY_SPECIAL[canon]
    return ' '.join('-'.join(p[:1].upper() + p[1:] for p in word.split('-')) for word in canon.split())


def find_club(name: str) -> tuple[str, str] | None:
    """Отображаемое имя + файл лого по свободному вводу, либо None."""
    key = normalize_club_name(name)
    if key in _ALIASES:
        canon = _ALIASES[key]
        if canon is None:
            return None
        key = canon
    for canon, file in TEAM_LOGO_MAP.items():
        if normalize_club_name(canon) == key:
            return display_name(canon), file
    # подстрочный фолбэк («манчестер» → первый манчестер)
    for canon, file in TEAM_LOGO_MAP.items():
        if key and key in normalize_club_name(canon):
            return display_name(canon), file
    return None


def logo_path_for(file: str) -> str:
    """Абсолютный путь к png лого (для раздачи фронту)."""
    import config
    return str(config.PROJECT_ROOT / "logovo-copy" / "src" / "assets" / "logos" / file)
