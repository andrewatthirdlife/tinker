from cart import Cart


def test_total_without_discount():
    cart = Cart()
    cart.add(10)
    cart.add(5.5)
    assert cart.total() == 15.5


def test_total_with_discount():
    cart = Cart(discount_percent=10)
    cart.add(100)
    assert cart.total() == 90
