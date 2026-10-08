from dates import parse_date


def year_of(text):
    return parse_date(text)[0]
