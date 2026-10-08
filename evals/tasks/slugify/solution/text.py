import re


def slugify(title):
    words = re.sub(r"[^a-z0-9 ]", "", title.lower()).split()
    return "-".join(words)
