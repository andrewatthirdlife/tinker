import pytest

from stack import EmptyStackError, Stack


def test_push_pop():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2 and len(s) == 1


def test_pop_empty():
    with pytest.raises(EmptyStackError):
        Stack().pop()


def test_error_is_an_index_error():
    assert issubclass(EmptyStackError, IndexError)
