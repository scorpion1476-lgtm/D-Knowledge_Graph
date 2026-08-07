import unittest

from shapes import make_circle, total_area


def build_fixture():
    return [make_circle(1.0), make_circle(2.0)]


class ShapeTests(unittest.TestCase):
    def test_area_is_positive(self):
        assert total_area(build_fixture()) > 0

    def test_circle_is_built(self):
        assert make_circle(3.0) is not None
