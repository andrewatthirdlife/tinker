from fizz import fizzbuzz


def test_thirty():
    result = fizzbuzz(30)
    assert len(result) == 30
    assert result[29] == "FizzBuzz" and result[8] == "Fizz" and result[9] == "Buzz" and result[10] == "11"


def test_zero():
    assert fizzbuzz(0) == []
