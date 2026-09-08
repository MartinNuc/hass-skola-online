"""Synthetic Škola Online grid markup.

The class names, nesting and tooltip format are copied from a live parent
account; the content is invented so no personal data lives in the repo.
"""

HEADER_CELL = (
    "<{tag}><table width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr><td>"
    '<div class="KuvHeaderNadpis">{number}</div>'
    '<div class="KuvHeaderText">{start} - {end}</div>'
    "</td></tr></table></{tag}>"
)

DAY_LABEL_CELL = (
    "<{tag}><div class=\"KuvDenNadpis\">{day}</div><div>{date}</div></{tag}>"
)


def week_html(
    rows: str,
    periods: list[tuple[int, str, str]] | None = None,
    header_tag: str = "td",
) -> str:
    """Wrap day rows in a full calendar table.

    header_tag selects the tag used for the corner cell and the period
    header cells. The live page uses "th" there; synthetic fixtures default
    to "td", which is also what earlier, pre-<th> fixtures used.
    """
    periods = periods or [
        (0, "07:40", "08:25"),
        (1, "08:30", "09:15"),
        (2, "09:25", "10:10"),
        (3, "10:30", "11:15"),
    ]
    header = "".join(
        HEADER_CELL.format(tag=header_tag, number=n, start=s, end=e)
        for n, s, e in periods
    )
    corner = f"<{header_tag}></{header_tag}>"
    return (
        '<html><body><table id="CCADynamicCalendarTable" class="DctTable">'
        f"<tr>{corner}{header}</tr>"
        f"{rows}"
        "</table></body></html>"
    )


def day_row(day: str, date_label: str, cells: str, label_tag: str = "td") -> str:
    """One day's row. label_tag selects the tag for the leading label cell.

    The live page uses "th" there; synthetic fixtures default to "td".
    """
    label = DAY_LABEL_CELL.format(tag=label_tag, day=day, date=date_label)
    return f"<tr>{label}{cells}</tr>"


EMPTY_CELL = '<td class="DctCellBottom DctCell"></td>'

TOOLTIP = (
    "onMouseOverTooltip('{abbrev} ({full}) ',"
    "'Učitel:~{teacher}~Třída:~{klass}~Žáci:~{klass} (celá třída)"
    "~Učebna:~{room}~Cyklus:~bez cyklů~Den (vyuč. hodina):~{day} ({period})')"
)

# DctInnerTableType10 is the class the live site uses for a taught lesson.
LESSON_CELL = (
    '<td class="DctCellBottom DctCell"{colspan}>'
    '<table class="DctInnerTableType10" onmouseover="{tooltip}">'
    '<tr><td class="DctInnerTableType10DataTD">'
    '<div class="KuvBunkaRozvrhNadpis">{abbrev}</div>'
    '<div class="KuvBunkaRozvrhText">{klass}{room}</div>'
    "</td></tr></table></td>"
)


def lesson_cell(
    abbrev: str,
    full: str,
    teacher: str,
    room: str,
    day: str,
    period: int,
    klass: str = "9.A",
    colspan: int = 1,
) -> str:
    tooltip = TOOLTIP.format(
        abbrev=abbrev, full=full, teacher=teacher, klass=klass, room=room,
        day=day, period=period,
    ).replace('"', "&quot;")
    return LESSON_CELL.format(
        tooltip=tooltip,
        abbrev=abbrev,
        klass=klass,
        room=room,
        colspan=f' colspan="{colspan}"' if colspan > 1 else "",
    )
