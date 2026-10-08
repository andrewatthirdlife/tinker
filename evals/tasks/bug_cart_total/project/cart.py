class Cart:
    def __init__(self, discount_percent=0):
        self.prices = []
        self.discount_percent = discount_percent

    def add(self, price):
        self.prices.append(price)

    def total(self):
        """The sum of the prices, less the discount."""
        for i, price in enumerate(self.prices):
            self.prices[i] = price * (100 - self.discount_percent) / 100
        return round(sum(self.prices), 2)
