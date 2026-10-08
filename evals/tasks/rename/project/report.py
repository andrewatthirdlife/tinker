from dates import parsedate


def year_of(text):
    return parsedate(text)[0]
