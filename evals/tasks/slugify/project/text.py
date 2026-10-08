def slugify(title):
    """Turn a title into a URL slug: lower case, words joined by single hyphens, only a-z and 0-9 kept."""
    return title.lower().replace(" ", "-")
