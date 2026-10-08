class EmptyStackError(IndexError):
    pass


class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise EmptyStackError("pop from an empty stack")
        return self._items.pop()

    def __len__(self):
        return len(self._items)
