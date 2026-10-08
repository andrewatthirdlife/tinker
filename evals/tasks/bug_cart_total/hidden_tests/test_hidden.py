from cart import Cart


def test_total_is_stable():
    cart = Cart(discount_percent=20)
    cart.add(50)
    cart.add(25)
    assert cart.total() == 60
    assert cart.total() == 60
    assert cart.prices == [50, 25]


def test_add_after_total():
    cart = Cart(discount_percent=50)
    cart.add(10)
    cart.total()
    cart.add(10)
    assert cart.total() == 10
