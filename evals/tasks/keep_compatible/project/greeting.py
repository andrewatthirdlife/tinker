from words import join_words


def greet(*names):
    return "Hello " + join_words(names)
