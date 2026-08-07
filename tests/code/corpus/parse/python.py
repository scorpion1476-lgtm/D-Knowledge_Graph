

class Animal:
    def eat(self):
        return 1


class Dog(Animal):
    def bark(self):
        return 2


def make_dog():
    return Dog()


def util_helper():
    return 3
